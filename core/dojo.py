"""The dojo: how JARVIS gets better at Blender by himself.

  Observe -> Create -> Test -> Fail -> Investigate -> Experiment -> Improve -> Verify -> Remember

* The JUDGE is fixed (hub/rig_acceptance.py + the pose tests); experiments cannot touch it (hub/overlay_guard.py).
* He proposes changes as parameter sets (always) or as small code overlays (guard-checked, sandboxed in the Blender
  subprocess, never written into the repo). The local language model proposes; measurements decide.
* A candidate is kept only if it improves the case it was made for AND every case that passed before still passes, checked
  through the exact production path (learned strategies run only after the default fit fails, best measured result wins).
* Everything is remembered in core.dojo_store (restart-proof); refuted ideas are remembered too and not blindly retried.
* "Mastered" is never claimed here: mastery() reports pass rates on cases whose failures were never studied.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from core import craftjobs, learn
from core.config import ROOT
from core.dojo_store import Store

HUB = ROOT / "hub"
CORPUS = ROOT / "docs" / "rigging" / "corpus.json"
DEFAULT_BUDGET = {"max_minutes": 60, "max_experiments": 12, "max_cases": 3, "rounds": 3, "per_run_timeout": 600,
                  "regression_seconds": 90, "research": True}


# ------------------------------------------------------------------ corpus + running the real engine
def resolve(p):
    return Path(p.replace("@", str(ROOT), 1) if p.startswith("@") else os.path.expanduser(p))


def corpus():
    return [dict(c, resolved=str(resolve(c["path"]))) for c in json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]]


def _params_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rig_params", HUB / "rig_params.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _guard_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("overlay_guard", HUB / "overlay_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare_source(store, path):
    """A GLB for any case (a .blend is converted once and cached by content time)."""
    p = Path(path)
    if p.suffix.lower() != ".blend":
        return str(p)
    from tools import rigging
    key = hashlib.sha1(f"{p}|{p.stat().st_mtime}".encode()).hexdigest()[:12]
    out = store.root / "cache" / f"{p.stem[:30]}-{key}.glb"
    if out.is_file():
        return str(out)
    out.parent.mkdir(exist_ok=True)
    exe = rigging.blender_exe()
    craftjobs.run([str(exe), "--background", "--factory-startup", "--python", str(HUB / "blend_to_glb.py"), "--", str(p), str(out)],
                  timeout=900, encoding="utf-8", errors="replace")
    if not out.is_file():
        raise RuntimeError(f"could not read a mesh from {p.name}")
    return str(out)


def score(report):
    """The fixed judge's verdict as a comparable record."""
    st = report.get("stages", {})
    from tools.remedies import stage_ok
    poses = report.get("pose_tests", []) + report.get("motion_tests", [])
    passed = sum(1 for t in poses if t["pass"])
    frac = passed / len(poses) if poses else 0.0
    stages_ok = sum(1 for v in st.values() if stage_ok(v))
    torn = sum(t.get("torn_fraction", 0) for t in poses)
    stray = sum(max(0.0, t.get("stray_fraction", 0) - 0.02) for t in poses)
    full = bool(st) and all(stage_ok(v) for v in st.values())
    return {"full": full, "stages_ok": stages_ok, "pose": f"{passed}/{len(poses)}", "pose_frac": round(frac, 4), "fail_count": len(poses) - passed, "total": len(poses),
            "torn": round(torn, 5), "stray_excess": round(stray, 4),
            "failing": [t["test"] for t in poses if not t["pass"]],
            "key": [int(full), stages_ok, round(frac, 4), -round(stray, 4), -round(torn, 5)]}


def better(a, b):
    """The shared improvement rule (core/learn.py): the same for every skill, hardened against the tie-break, ratio and deletion hacks."""
    return learn.better(a, b)


def at_least_as_good(a, b):
    return a["key"] >= b["key"]


def run_case(store, case, params=None, overlay=None, variants=None, faces=40000, timeout=600, tag="exp"):
    """One real Blender run of the production engine on a case. Returns {score, report, folder, seconds} or {error}."""
    from tools import rigging
    t0 = time.time()
    src = prepare_source(store, case["resolved"])
    work = store.root / "runs" / f"{case['name']}-{tag}-{int(t0)}"
    work.mkdir(parents=True, exist_ok=True)
    creature = case.get("kind") == "creature"
    cmd = [str(rigging.blender_exe()), "--background", "--factory-startup", "--python",
           str(HUB / ("creature_acceptance.py" if creature else "rig_acceptance.py")), "--",
           src, str(work), "--faces", str(faces)] + (["--kind", case["kind"]] if case.get("kind") and not creature else [])
    if params:
        cmd += ["--params", json.dumps(params)]
    if overlay:
        (work / "overlay.py").write_text(overlay, encoding="utf-8")
        cmd += ["--overlay", str(work / "overlay.py")]
    if variants:
        (work / "variants.json").write_text(json.dumps(variants), encoding="utf-8")
        cmd += ["--variants", str(work / "variants.json")]
    try:
        craftjobs.run(cmd, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s", "seconds": round(time.time() - t0, 1)}
    rf = work / "report.json"
    if not rf.is_file():
        return {"error": "no report (crash)", "seconds": round(time.time() - t0, 1)}
    report = json.loads(rf.read_text(encoding="utf-8"))
    if report.get("overlay_rejected"):
        return {"error": "overlay rejected: " + "; ".join(report["overlay_rejected"])}
    return {"score": score(report), "report": report, "folder": str(work), "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ evidence + the local language model
def evidence(case, run):
    """What went wrong, from measurements only (no opinions): the investigator's briefing."""
    rep, sc = run["report"], run["score"]
    pipe = rep.get("pipeline", {})
    lm = pipe.get("landmarks", {})
    bad = [{k: t.get(k) for k in ("test", "moved_fraction", "stray_fraction", "edge_stretch_p99", "torn_fraction", "edge_compress_p0.1",
                                  "stray_top_bones", "stray_centroid_over_H", "subtree_verts")}
           for t in (rep.get("pose_tests", []) + rep.get("motion_tests", [])) if not t["pass"]][:14]
    return {"case": case["name"], "type": case.get("type"), "score": {k: sc[k] for k in ("pose", "failing", "torn")},
            "stages": rep.get("stages"), "failing_tests": bad,
            "analysis": {k: pipe.get("analysis", {}).get(k) for k in ("kind", "confidence", "faces", "dimensions", "leg_clusters")},
            "landmarks": {k: lm.get(k) for k in ("H", "crotch_z", "neck_z", "torso_half", "leg_off", "arm_tips", "arm_fit", "fit_source")},
            "pruned_bones": len(pipe.get("pruned_bones") or []), "skin_attempts": [(a["method"], a.get("validation", {}).get("ok")) for a in pipe.get("skin", {}).get("attempts", [])],
            "retopo": pipe.get("retopo"),
            **({"anatomy_found": pipe.get("anatomy"), "branches": [{k: b.get(k) for k in ("role", "length", "radius", "flat", "sheet_share", "lat", "drop", "parent")} for b in (pipe.get("branches") or [])][:24]}
               if pipe.get("anatomy") else {})}


def signature(ev):
    """Tokens that characterise a failure, for recalling relevant past lessons."""
    toks = set()
    for t in ev.get("failing_tests", []):
        toks.add("fail:" + t["test"].split(".")[0])
        if (t.get("stray_fraction") or 0) > 0.02:
            toks.add("stray")
        if (t.get("torn_fraction") or 0) > 0.0015:
            toks.add("torn")
    for m, ok in ev.get("skin_attempts", []):
        if not ok:
            toks.add("skinfail:" + m)
    if (ev.get("analysis", {}).get("faces") or 0) > 1_000_000:
        toks.add("dense")
    toks.add("type:" + str(ev.get("type")))
    return sorted(toks)


def _ollama():
    from core import engeyes
    return engeyes._ollama_base()


def llm_model():
    from core import engeyes
    return os.getenv("JARVIS_DOJO_MODEL") or engeyes.model()


def ask_json(prompt, schema, images=None, temperature=0.4, timeout=240):
    """One structured answer from the LOCAL model (free, on this PC). Raises RuntimeError when it can't."""
    name = llm_model()
    if not name:
        raise RuntimeError("no local model available")
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = images
    from core import warmth
    # The brain's num_ctx/keep-alive (another size reloads it) and thinking off; the schema is in the prompt as well,
    # because Ollama drops "format" for qwen3.5 when thinking is off (ollama#14645).
    msg["content"] = f"{prompt}\n\nReply with JSON only, matching this JSON schema: {json.dumps(schema, separators=(',', ':'))}"
    body = {"model": name, "messages": [msg], "stream": False, "format": schema, **warmth.ollama_fields(name, "10m")}
    body["options"]["temperature"] = temperature
    req = urllib.request.Request(_ollama() + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    last = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                content = json.loads(r.read().decode())["message"]["content"]
            return json.loads(content[content.find("{"): content.rfind("}") + 1])
        except Exception as e:
            last = e
    raise RuntimeError(f"local model failed: {type(last).__name__}")


def function_sources(names):
    """Source of replaceable engine functions, so a code patch starts from what actually exists."""
    text = (HUB / "rigging.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out.append("\n".join(lines[node.lineno - 1: node.end_lineno]))
    return "\n\n".join(out)


HYP_SCHEMA = {"type": "object", "properties": {"hypotheses": {"type": "array", "items": {"type": "object", "properties": {
    "cause": {"type": "string"}, "evidence_used": {"type": "string"}, "kind": {"type": "string", "enum": ["params", "patch"]},
    "params": {"type": "object"}, "function": {"type": "string"}, "source": {"type": "string"}, "expected": {"type": "string"}},
    "required": ["cause", "kind", "expected"]}}}, "required": ["hypotheses"]}


def knob_table():
    mod = _params_mod()
    rows = []
    for k, (d, lo, hi) in mod.PARAM_SPEC.items():
        rows.append(f"- {k} (default {d}, allowed {lo}..{hi}): {mod.PARAM_DOC[k]}")
    return "\n".join(rows)


def propose(ev, lessons, mode, research="", ask=ask_json, creature=False):
    """Ask the local model for up to 3 testable hypotheses. mode 'params' = knobs only; 'any' = knobs or a code patch."""
    bad_names = sorted({w for l in lessons for w in re.findall(r"unknown parameter (\w+)", str(l.get("delta", "")))})
    seen = "\n".join(f"- {l.get('hypothesis', '')[:140]} => {l.get('status')} ({l.get('delta', '')})" for l in lessons[:10]) or "(nothing yet)"
    patch = ""
    if mode == "any":
        names = {"anatomy_refine_roles"} if creature else {"humanoid_landmarks", "humanoid_spec"} if any(t["test"].split(".")[0] in ("shoulder", "elbow", "wrist", "fingers", "hip", "knee")
                                                                for t in ev["failing_tests"]) else {"skin_nearest_segment", "prune_unsupported"}
        hook = ("\n\nFOR CREATURES, the best place for a patch is the small hook anatomy_refine_roles(roles, info, nodes): roles is a dict "
                "{branch_index: role}, role one of axial/leg/arm/wing/digit/appendage/drop; info is a dict {branch_index: {length, radius, flat, "
                "sheet_share, lat, drop, tip_z, parent, attach_frac}} (parent is the branch it grows from or None for the body); nodes is a list "
                "of {length,...}. Return a NEW dict with the same keys. Use plain Python only. Decide from the evidence what, if anything, to change." if creature else "")
        patch = (hook + "\n\nYou may instead propose a CODE PATCH: kind 'patch', function = the name of ONE function to replace (allowed: humanoid_landmarks, "
                 "humanoid_spec, quadruped_spec, prune_unsupported, skin_nearest_segment, clean_weights, decimate_copy, analyze, clean_mesh, "
                 "transfer_weights, anatomy_refine_roles), source = the COMPLETE new function (Python, numpy/math/bmesh/mathutils only, no files or network; read tunables "
                 "with P_('name')). Current source:\n" + function_sources(names)[:7000])
    prompt = (
        "You are the rigging engineer inside JARVIS. An automatic rigging pipeline failed its measured tests on a 3D character. "
        "Find the most likely CAUSE from the measurements and propose up to 3 different, testable changes. Do not repeat ideas already tried.\n\n"
        f"MEASURED EVIDENCE (JSON):\n{json.dumps(ev, default=str)[:5000]}\n\n"
        f"WHAT WAS ALREADY TRIED (and what happened):\n{seen}\n\n"
        + (f"HINTS FROM THE WEB (unverified, may be wrong):\n{research[:1500]}\n\n" if research else "")
        + (f"THESE NAMES DO NOT EXIST and were rejected before, never use them: {', '.join(bad_names)}.\n\n" if bad_names else "")
        + f"TUNABLE PARAMETERS (use kind 'params' with a params object of ONLY these names):\n{knob_table()}"
        + patch + "\n\nBody-height-relative numbers: H is the body height. Return JSON {hypotheses:[...]}. Each hypothesis: cause, evidence_used, kind, "
        "params or function+source, expected (what you predict will change).")
    out = ask(prompt, HYP_SCHEMA)
    return [h for h in out.get("hypotheses", []) if isinstance(h, dict)][:3]


def check_hypothesis(h):
    """-> (ok, reason, params, overlay_source, change_key). Everything an experiment will run passes through here."""
    kind = h.get("kind")
    if kind == "params":
        params = h.get("params") or {}
        err = _params_mod().validate_params(params)
        if err or not params:
            return False, err or "empty params", None, None, None
        return True, "", params, None, "params:" + json.dumps(params, sort_keys=True)
    if kind == "patch":
        src = h.get("source") or ""
        ok, problems, defined = _guard_mod().check(src)
        if not ok:
            return False, "; ".join(problems)[:300], None, None, None
        if h.get("function") and h["function"] not in defined:
            return False, f"source does not define {h['function']}", None, None, None
        return True, "", None, src, "patch:" + hashlib.sha1(src.encode()).hexdigest()[:12]
    return False, "unknown hypothesis kind", None, None, None


def research_hints(ev, ask=ask_json):
    """One web search for how experienced riggers handle this symptom. Hints only: never trusted, never acted on directly."""
    try:
        q = ask("Write ONE short web search query (max 12 words) to find how experienced 3D riggers fix this problem with automatic "
                f"character rigging in Blender. Problem measurements: {json.dumps(ev.get('failing_tests'), default=str)[:700]}; "
                f"analysis: {json.dumps(ev.get('analysis'), default=str)}",
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, temperature=0.2)["query"]
        from core import websearch
        res = websearch.search(q, 4)
        pack = res.get("pack") if isinstance(res, dict) and "pack" in res else res
        return q, websearch.evidence_text(pack)[:1800]
    except Exception:
        return "", ""


# ------------------------------------------------------------------ the practice loop
def _gaming():
    try:
        from tools import pcops
        return bool(pcops.processes_matching(*craftjobs.GAME_PROCESSES))
    except Exception:
        return False


class Practice:
    """One practice session on its own thread. Yields to real work (a rig job) and to a game; stops on the kill switch."""

    def __init__(self, store=None, budget=None, runner=run_case, asker=ask_json, gaming=_gaming, busy=lambda: craftjobs._running["id"]):
        self.store = store or Store()
        self.budget = {**DEFAULT_BUDGET, **(budget or {})}
        self.runner, self.asker, self.gaming, self.busy = runner, asker, gaming, busy
        self.cancel = threading.Event()
        self.thread = None
        self.result = None
        self.t0 = 0.0
        self.n_exp = 0

    # --- control
    def start(self):
        self.store.clear_stop()
        self.t0 = time.time()
        self.store.set_state(running=True, started=time.strftime("%Y-%m-%dT%H:%M:%S"), budget=self.budget, step="starting", experiments=0)
        self.thread = threading.Thread(target=self._main, daemon=True, name="dojo")
        self.thread.start()
        return self

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)
        return self.result

    def stop(self):
        self.store.request_stop()
        self.cancel.set()

    def _should_stop(self):
        if self.cancel.is_set() or self.store.stop_requested():
            return "stopped by owner"
        if time.time() - self.t0 > self.budget["max_minutes"] * 60:
            return "time budget used"
        if self.n_exp >= self.budget["max_experiments"]:
            return "experiment budget used"
        if self.gaming():
            return "a game is running (the GPU and CPU are yours)"
        return ""

    def _wait_if_busy(self):
        """Real jobs first: wait (bounded) while a rig/generate job holds the machine."""
        waited = 0
        while self.busy() and waited < 1800 and not self.cancel.is_set():
            self.store.set_state(step="waiting for your job to finish")
            time.sleep(2)
            waited += 2

    def _step(self, text):
        self.store.set_state(step=text, experiments=self.n_exp)

    # --- the loop
    def _main(self):
        craftjobs._tls.job = {"_procs": [], "_cancel": self.cancel, "started": self.t0, "steps": [], "step": ""}   # cancel kills Blender
        summary = {"cases": [], "promoted": 0, "experiments": 0, "stopped": ""}
        try:
            summary["demoted"] = audit_playbook(self.store)
            targets = self.pick_targets()
            summary["targets"] = [c["name"] for c in targets]
            for case in targets[: self.budget["max_cases"]]:
                why = self._should_stop()
                if why:
                    summary["stopped"] = why
                    break
                summary["cases"].append(self.work_on(case))
            summary["promoted"] = sum(c.get("promoted", 0) for c in summary["cases"])
        except craftjobs.Cancelled:
            summary["stopped"] = "stopped by owner"
        except Exception as e:                                        # noqa: BLE001
            summary["stopped"] = f"crashed: {type(e).__name__}: {str(e)[:160]}"
            self.store.event("session_error", error=summary["stopped"])
        summary["experiments"] = self.n_exp
        summary["stopped"] = summary["stopped"] or "finished its plan"
        summary["minutes"] = round((time.time() - self.t0) / 60, 1)
        self.result = summary
        self.store.set_state(running=False, step="idle", last_summary=summary, finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.store.event("session_end", **{k: v for k, v in summary.items() if k != "cases"})
        craftjobs._tls.job = None

    def pick_targets(self):
        seed_cases(self.store)
        sync_cases(self.store)
        """Cases that currently fail (never-studied failures first). Passing cases are the regression guard, not targets."""
        cases = corpus()
        out = []
        only = self.budget.get("only")
        for c in cases:
            if not Path(c["resolved"]).is_file() or (only and c["name"] not in only):
                continue
            st = self.store.case(c["name"])
            if st.get("ok") is True:
                continue
            out.append((st.get("studied", 0), c))
        out.sort(key=lambda t: t[0])
        return [c for _, c in out]

    def baseline(self, case):
        self._step(f"measuring {case['name']} with what I know now")
        run = self.runner(self.store, case, variants=self.store.runtime_variants(include_pending=True), timeout=self.budget["per_run_timeout"], tag="base")
        if "error" in run:
            return None
        self.store.update_case(case["name"], ok=run["score"]["full"], last=run["score"], seconds=run["seconds"])
        return run

    def work_on(self, case):
        res = {"case": case["name"], "promoted": 0, "experiments": [], "final": None}
        best = self.baseline(case)
        if best is None:
            res["note"] = "baseline run failed"
            return res
        res["before"] = best["score"]["pose"]
        if best["score"]["full"]:
            res["note"] = "already passes with learned strategies"
            res["final"] = best["score"]["pose"]
            return res
        research, hint_q = "", ""
        for rnd in range(self.budget["rounds"]):
            if self._should_stop():
                break
            self._wait_if_busy()
            ev = evidence(case, best)
            if rnd == 0 and "anatomy_found" in ev:
                ev.update(self.creature_context(case, best))
            sig = signature(ev)
            if rnd == 0 and self.budget.get("research"):
                self._step("looking up how others solve this")
                hint_q, research = research_hints(ev, self.asker)
                self.store.event("research", case=case["name"], query=hint_q, got=bool(research))
            self._step(f"working out why {case['name']} fails")
            try:
                mode = "any" if (rnd > 0 or self.budget.get("force_patch")) else "params"
                hyps = propose(ev, self.store.relevant_lessons(sig), mode, research, self.asker, creature="anatomy_found" in ev)
            except RuntimeError as e:
                res["note"] = f"investigator unavailable: {e}"
                break
            self.store.update_case(case["name"], studied=self.store.case(case["name"]).get("studied", 0) + 1, burned=True)
            if not hyps:
                res["note"] = "the investigator had no new ideas"
                break
            for h in hyps:
                if self._should_stop():
                    break
                self._wait_if_busy()
                ok, reason, params, overlay, ckey = check_hypothesis(h)
                base_l = {"case": case["name"], "signature": sig, "hypothesis": (h.get("cause") or "")[:300], "expected": (h.get("expected") or "")[:200]}
                if not ok:
                    self.store.add_lesson(**base_l, status="rejected", delta=reason[:200], change_key=None)
                    res["experiments"].append({"status": "rejected", "why": reason[:120]})
                    continue
                if ckey in self.store.tried(case["name"]):
                    continue
                self._step(f"testing an idea on {case['name']}: {(h.get('cause') or '')[:60]}")
                run = self.runner(self.store, case, params=params, overlay=overlay, timeout=self.budget["per_run_timeout"], tag="exp")
                self.n_exp += 1
                if "error" in run:
                    self.store.add_lesson(**base_l, status="error", delta=run["error"][:160], change_key=ckey, change=params or "code patch")
                    res["experiments"].append({"status": "error", "why": run["error"][:100]})
                    continue
                delta = f"{best['score']['pose']} -> {run['score']['pose']} tests passing"
                if not better(run["score"], best["score"]):
                    self.store.add_lesson(**base_l, status="refuted", delta=delta, change_key=ckey, change=params or "code patch")
                    res["experiments"].append({"status": "refuted", "delta": delta})
                    continue
                verdict = self.verify(case, params, overlay)
                if verdict["ok"]:
                    row = self.store.add_variant(params or {}, overlay, evidence={"case": case["name"], "before": best["score"]["pose"],
                                                                                    "after": run["score"]["pose"], "regression_checked": verdict["checked"],
                                                                                    "hypothesis": base_l["hypothesis"]}, name=(h.get("cause") or "")[:60])
                    self.store.add_lesson(**base_l, status="verified", delta=delta, change_key=ckey, change=params or "code patch", variant=row["id"],
                                          regression_checked=verdict["checked"])
                    self.store.event("promoted", variant=row["id"], case=case["name"], delta=delta)
                    res["promoted"] += 1
                    res["experiments"].append({"status": "verified", "delta": delta, "variant": row["id"]})
                    best = verdict["run"]
                    self.store.update_case(case["name"], ok=best["score"]["full"], last=best["score"])
                    if best["score"]["full"]:
                        break
                else:
                    self.store.add_lesson(**base_l, status="regressed", delta=f"{delta}, but {verdict['why']}", change_key=ckey, change=params or "code patch")
                    res["experiments"].append({"status": "regressed", "why": verdict["why"][:120]})
            if best["score"]["full"]:
                break
        res["final"] = best["score"]["pose"]
        res["passes_now"] = best["score"]["full"]
        return res

    def creature_context(self, case, run):
        """Visual second opinion + trusted-source expectations for a creature, added to the evidence (both uncertain)."""
        from core import anatomy_knowledge, dojo_vision
        ctx = {}
        plan = (run["report"].get("pipeline") or {}).get("anatomy") or {}
        if self.budget.get("vision", True):
            self._step("looking at the rig with my vision model")
            v = dojo_vision.review(run["folder"], plan, self.asker)
            if v and "error" not in v:
                ctx["visual_review"] = {k: v.get(k) for k in ("creature_guess", "counts", "skeleton_covers_body", "bones_outside_body", "problems", "confidence", "disagrees_on")}
                self.store.event("visual_review", case=case["name"], guess=v.get("creature_guess"), disagrees=v.get("disagrees_on"))
                guess = (v.get("creature_guess") or "").strip()
                if guess and self.budget.get("research", True):
                    card = self.store.card_for(guess)
                    if card is None:
                        self._step(f"researching what a {guess[:30]} looks like inside")
                        card = anatomy_knowledge.research_creature(guess, self.store, self.asker)
                    if card:
                        ctx["reference_card"] = {k: card.get(k) for k in ("creature", "typical_counts", "body_segments", "movement_modes", "status")}
                        ctx["reference_mismatches"] = anatomy_knowledge.expectations(card, plan)
        return ctx

    def verify(self, case, params, overlay):
        """Production-path check: the candidate as a learned strategy must (a) still help the target and (b) leave every
        case that passed before still passing."""
        cand = [{"id": "candidate", "params": params or {}, "overlay_source": overlay}]
        self._step(f"verifying the idea through the real pipeline on {case['name']}")
        run = self.runner(self.store, case, variants=cand + self.store.runtime_variants(include_pending=True), timeout=self.budget["per_run_timeout"], tag="ver")
        if "error" in run:
            return {"ok": False, "why": "verification run failed: " + run["error"][:80]}
        before = self.store.case(case["name"]).get("last") or {"key": [0, 0, 0, 0, 0]}
        if not at_least_as_good(run["score"], before):
            return {"ok": False, "why": "production path did not reproduce the improvement"}
        checked = []
        for other in corpus():
            if other["name"] == case["name"] or not Path(other["resolved"]).is_file():
                continue
            st = self.store.case(other["name"])
            if st.get("ok") is not True or (st.get("seconds") or 0) > self.budget["regression_seconds"]:
                continue
            if self._should_stop():
                break
            self._step(f"checking the idea did not break {other['name']}")
            r2 = self.runner(self.store, other, variants=cand + self.store.runtime_variants(include_pending=True), timeout=self.budget["per_run_timeout"], tag="reg")
            checked.append(other["name"])
            if "error" in r2 or not r2["score"]["full"]:
                return {"ok": False, "why": f"it broke {other['name']}", "checked": checked}
        return {"ok": True, "run": run, "checked": checked}


def audit_playbook(store):
    """Demote learned strategies whose own evidence shows no meaningful improvement (self-correction of earlier mistakes)."""
    demoted = []
    for v in store.playbook():
        if v["status"] != "active":
            continue
        ev = v.get("evidence") or {}
        try:
            (b, tb), (a, ta) = ((int(x.split("/")[0]), int(x.split("/")[1])) for x in (ev["before"], ev["after"]))
        except Exception:
            continue
        if ta < 0.8 * tb or not (ta - a < tb - b or a == ta):     # a shrunken skeleton, or no fewer failing tests (unless all pass)
            store.set_variant_status(v["id"], "quarantined", f"evidence {ev['before']} -> {ev['after']} is not a real improvement")
            store.add_lesson(case=ev.get("case"), signature=[], hypothesis=f"strategy {v['id']} ({v.get('name', '')[:80]})", status="demoted",
                             delta=f"{ev['before']} -> {ev['after']}: a tie-break difference is not an improvement", change_key=None)
            store.event("demoted", variant=v["id"])
            demoted.append(v["id"])
    return demoted


def sync_cases(store):
    """Add benchmark results for cases the store has not seen yet (the corpus grows; the memory must keep up)."""
    f = ROOT / "data" / "rigging" / "scorecards" / "latest.json"
    if not f.is_file():
        return
    card = json.loads(f.read_text(encoding="utf-8"))
    known = store.cases()
    for name, r in card.get("cases", {}).items():
        if name in known or r.get("ok") is None:
            continue
        store.update_case(name, ok=bool(r["ok"]), seconds=r.get("seconds"), seeded_from="benchmark " + str(card.get("ts", ""))[:10])


def seed_cases(store):
    """First run only: take what the benchmark already measured (pass/fail, speed, which failures were studied)."""
    if store.cases():
        return
    f = ROOT / "data" / "rigging" / "scorecards" / "latest.json"
    if not f.is_file():
        return
    card = json.loads(f.read_text(encoding="utf-8"))
    for name, r in card.get("cases", {}).items():
        if r.get("ok") is None:
            continue
        store.update_case(name, ok=bool(r["ok"]), seconds=r.get("seconds"), burned=(r.get("split") == "heldout" and not r["ok"]) or None,
                          seeded_from="benchmark 2026-10-31")


# ------------------------------------------------------------------ reporting
def mastery(store=None):
    """Honest pass rates per body kind, on cases whose failures were NEVER studied. A label needs evidence; none is given here."""
    store = store or Store()
    rows = {}
    for c in corpus():
        st = store.case(c["name"])
        if st.get("ok") is None:
            continue
        kind = c.get("kind") or "unknown"
        r = rows.setdefault(kind, {"cases": 0, "passing": 0, "fresh_cases": 0, "fresh_passing": 0})
        r["cases"] += 1
        r["passing"] += 1 if st["ok"] else 0
        if not st.get("burned") and c.get("split") == "heldout":
            r["fresh_cases"] += 1
            r["fresh_passing"] += 1 if st["ok"] else 0
    for k, r in rows.items():
        r["label"] = ("unproven: fewer than 5 never-studied cases" if r["fresh_cases"] < 5
                      else ("meets 90% on fresh cases" if r["fresh_passing"] / r["fresh_cases"] >= 0.9 else "below 90% on fresh cases"))
    return rows


def spoken_learned(store=None):
    store = store or Store()
    ls, pb = store.lessons(), store.playbook()
    if not ls:
        return "I have not practised yet, so there is nothing learned to report. Say 'practice rigging' and I will start."
    by = {}
    for l in ls:
        by[l["status"]] = by.get(l["status"], 0) + 1
    active = [v for v in pb if v["status"] == "active"]
    live = {v["id"] for v in active}
    last = [l for l in ls if l["status"] == "verified" and l.get("variant") in live][-3:]
    parts = [f"I have run {len(ls)} experiments: {by.get('verified', 0)} worked and were kept, {by.get('refuted', 0)} were disproved, "
             f"{by.get('regressed', 0)} helped one character but broke another, {by.get('rejected', 0)} were rejected as unsafe or invalid, "
             f"{by.get('demoted', 0)} earlier strategies I demoted after checking my own evidence, "
             f"{by.get('error', 0)} failed to run."]
    if active:
        parts.append(f"I now carry {len(active)} learned {'strategy' if len(active) == 1 else 'strategies'} that I try when my default fit fails.")
    for l in last:
        parts.append(f"For example on {l['case']}: {l['hypothesis'][:140]} ({l['delta']}).")
    return " ".join(parts)
