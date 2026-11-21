"""Character rigging skills: JARVIS rigs a 3D model himself, headlessly, and learns from every run.

rig_character      model file -> clean, classify (humanoid/quadruped), fitted skeleton (fingers, IK/FK),
                   validated skinning with automatic repair, MEASURED pose/IK/export/round-trip tests,
                   Blender+Unreal-ready exports. One fixed job: no model-written code, nothing overwritten,
                   output only inside a new unique folder, zero paid calls.
rigging_skills     honest per-capability status derived from real run evidence (never self-declared).
rigging_lessons    reusable lessons recorded by real runs, so the same problem is not solved twice.

A capability is "verified" only when a real Blender run measured it passing; it is never "mastered".
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from core import craftjobs, storage
from core.config import ROOT
from tools.registry import tool

HUB = ROOT / "hub"
LEDGER = "blender/rigging_ledger.json"
MODEL_EXT = (".glb", ".gltf")
CAPABILITIES = ("skin_valid", "pose_tests", "ik_fk", "export", "reimport_animation_roundtrip")
BLENDER_GLOBS = ("C:/Program Files/Blender Foundation/Blender */blender.exe",)


def blender_exe():
    env = os.getenv("JARVIS_BLENDER_EXE", "")
    if env and Path(env).is_file():
        return Path(env)
    from glob import glob
    found = sorted(glob(BLENDER_GLOBS[0]), reverse=True)
    return Path(found[0]) if found else None


def _allowed_roots():
    roots = [storage.DATA, Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace"))]
    extra = os.getenv("JARVIS_MODEL_ROOTS", "")
    roots += [Path(p) for p in extra.split(os.pathsep) if p]
    roots.append(Path.home() / "Downloads")
    return [r.resolve() for r in roots]


def _ledger():
    return storage.load_global(LEDGER, {"runs": [], "lessons": []})


def _save(led):
    storage.save_global(LEDGER, led)


def _lessons_from(report):
    """Turn what a real run did into short, reusable, factual lessons."""
    p, out = report.get("pipeline", {}), []
    skin = p.get("skin", {})
    used = skin.get("final_method")
    if used and used != "armature_auto_direct":
        out.append(f"Direct heat-weighting failed validation; '{used}' repaired it. Check for disconnected shells or overlapping parts.")
    if p.get("pruned_bones"):
        out.append(f"{len(p['pruned_bones'])} finger/toe bones had no mesh support and were pruned (hands without separable fingers are normal for gloves/claws).")
    if p.get("finger_density_boost_faces"):
        out.append("Fingers need a dense game mesh (>=150k faces) to take weights; the pipeline raised density automatically.")
    for h in p.get("verification", []):
        if "pose_pass" not in h:
            continue                         # creature runs record their strategies differently
        if h["pose_pass"].split("/")[0] != h["pose_pass"].split("/")[1] and h["strategy"] != "fit":
            out.append(f"Landmark strategy '{h['strategy']}' was needed after pose tests failed ({h['pose_pass']}).")
    src_faces = p.get("analysis", {}).get("faces", 0)
    if src_faces > 150000:
        out.append(f"Generator mesh had {src_faces} faces: never skin that directly (740k-face auto-weights took ~50 s); skin a game-res copy.")
    fit = p.get("landmarks", {}).get("fit_source", {})
    if isinstance(fit, dict) and "implausible" in str(fit.get("crotch", "")):
        out.append("Clothing (coat/skirt) hid the gap between the legs; crotch height came from anatomical proportions instead.")
    return out


def record_run(report, source, out_dir, ok):
    led = _ledger()
    led["runs"].append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "input": str(source),
                        "kind": report.get("pipeline", {}).get("kind_used"), "stages": report.get("stages", {}),
                        "report": str(Path(out_dir) / "report.json"), "ok": ok,
                        "bones": report.get("pipeline", {}).get("bones"),
                        "fingers_tested": any("finger" in t["test"] for t in report.get("pose_tests", []))})
    known = {l["text"] for l in led["lessons"]}
    for text in _lessons_from(report):
        if text not in known:
            led["lessons"].append({"ts": led["runs"][-1]["ts"], "text": text, "source": str(source)})
    led["runs"] = led["runs"][-200:]
    _save(led)
    return led


def _stage_ok(name, value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and "/" in value and "passed" in value:
        a, b = value.split()[0].split("/")
        return a == b and int(b) > 0
    return value == "PASS"


def skills_status():
    led = _ledger()
    status = {}
    for cap in CAPABILITIES + ("fingers",):
        rel = [r for r in led["runs"] if cap != "fingers" or r.get("fingers_tested")]
        if cap == "fingers":
            passed = [r for r in rel if _stage_ok("pose_tests", r["stages"].get("pose_tests"))]
        else:
            passed = [r for r in rel if _stage_ok(cap, r["stages"].get(cap))]
        failed = [r for r in rel if r not in passed]
        kinds = sorted({r["kind"] for r in passed if r.get("kind")})
        if not rel:
            state = "untested"
        elif not passed:
            state = "failed"
        else:
            state = "verified"
        status[cap] = {"state": state, "passed_runs": len(passed), "failed_runs": len(failed), "kinds": kinds}
    status["note"] = "verified = measured passing in real Blender runs; nothing here is 'mastered'. Not covered: Unreal import, image-to-3D in this chain unless a run says so."
    return status


def _sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(out, src, report, ok, seconds):
    """One manifest.json per run: what went in, what came out (hashes), what was measured. Output hygiene and provenance."""
    files = {f.name: {"bytes": f.stat().st_size, "sha256": _sha(f)} for f in sorted(out.iterdir()) if f.suffix in (".glb", ".fbx")}
    m = {"schema": 1, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": ok,
         "source": {"path": str(src), "sha256": _sha(src) if Path(src).is_file() else None},
         "blender": report.get("blender"), "kind": (report.get("pipeline") or {}).get("kind_used"),
         "profile_ue5": "bone_name_map" in report and bool(report.get("bone_name_map")),
         "stages": report.get("stages"), "seconds": seconds, "files": files,
         "pruned_bones": (report.get("pipeline") or {}).get("pruned_bones") or None}
    (out / "manifest.json").write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
    return m


def _unreal_gate(out, report):
    """Import the exported FBX into headless Unreal and measure what UE sees. None when Unreal is not installed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ue_check", HUB / "ue_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fbx = out / "char_rigged.fbx"
    if mod.ue_cmd() is None or not fbx.is_file() or os.getenv("JARVIS_SKIP_UNREAL") == "1":
        return None
    try:                                  # Mark V guard: no game running, no editor on that project, < 3 Unreal processes
        from core import unreal as _ue
        import tempfile                   # the path only: project_dir() would create the project before the check
        gate_dir = Path(os.getenv("JARVIS_UE_GATE_PROJECT") or (Path(tempfile.gettempdir()) / "jarvis_ue_gate"))
        allowed, why = _ue.may_run_headless(str(gate_dir / "Gate.uproject"))
    except Exception as e:  # noqa: BLE001
        allowed, why = False, f"I couldn't check Unreal's state ({type(e).__name__})"
    if not allowed:
        return {"ok": None, "skipped": why}
    expected = (report.get("reimport") or {}).get("verts_original")
    res = mod.run(runner=craftjobs.run, cases=[{"name": re.sub(r"[^A-Za-z0-9_]", "_", out.name)[:40], "fbx": str(fbx), "expected_verts": expected}])
    case = (res.get("cases") or [{}])[0]
    return {"ok": bool(res.get("ok")), **{k: case.get(k) for k in ("verts", "verts_expected", "bones", "roots", "single_root", "animated_bones", "skin", "skin_ok", "error")},
            "error_run": res.get("error")}


GAME_MAX_FACES = 70000    # Mark V: a UE5 game character's face budget (~47k vertices in Unreal for a textured Meshy body; the
                          # Shadow World gate is 60k); it caps the finger-density boost, which kept 106k faces before


def _rig(model_path: str, kind: str = "", profile: str = "ue5", target_faces: int = 40000, max_faces: int = 0):
    """Rig with a bounded trial-and-error loop: on a failure, classify it, apply the best-ranked untried remedy
    (kind / mesh density), re-run, keep the best attempt, and record what worked so the ranking improves."""
    from tools import remedies as R
    attempts, tried_params, tried_remedies = [], set(), set()
    cur_kind, cur_faces, src = kind, int(target_faces), model_path
    best, pending = None, None
    for n in range(1, R.MAX_ATTEMPTS + 1):
        craftjobs.check_cancel()
        budget = max_faces or (GAME_MAX_FACES if profile == "ue5" else 0)
        res = _rig_once(src, cur_kind, profile, cur_faces, **({"max_faces": budget} if budget else {}))
        craftjobs.check_cancel()
        code = R.classify(res)
        led = _ledger()
        if pending:                                   # learn: did the remedy we just applied fix the previous failure?
            R.record(led.setdefault("remedies", {}), pending[0], pending[1], code is None and not isinstance(res, str))
            _save(led)
        used_kind = res.get("kind") if isinstance(res, dict) else (cur_kind or "humanoid")
        attempts.append({"n": n, "kind": used_kind, "faces": cur_faces, "ok": isinstance(res, dict) and bool(res.get("ok")), "failure": code})
        if isinstance(res, dict):
            src = res.get("converted_glb") or src            # never re-convert the .blend on a retry
        if best is None or R.score(res) > R.score(best):
            best = res
        if code is None:
            break
        tried_params.add((used_kind, cur_faces))
        step = R.next_step(code, used_kind, cur_faces, _ledger().get("remedies", {}), tried_params, tried_remedies)
        if step is None:
            break
        remedy, cur_kind, cur_faces = step
        tried_remedies.add(remedy)
        pending = (code, remedy)
        attempts[-1]["next_remedy"] = remedy
        craftjobs.step(f"attempt {n} failed ({code}); trying again: {R.REMEDIES[remedy]}")
    if isinstance(best, dict):
        best["attempts"] = attempts
        if best.get("ok") and best.get("output_folder"):     # every finished rig also lands where the owner looks
            try:
                from tools import charjobs
                docs = charjobs.copy_to_documents(best["output_folder"])
            except Exception:  # noqa: BLE001  (a failed copy never fails the rig; the workspace folder is still named)
                docs = None
            if docs is not None:
                best["documents_folder"] = str(docs)
    return best


def _docs_sentence(d):
    return (f"A copy is in your Documents folder, in JARVIS Rigged Characters, in a folder called {Path(d['documents_folder']).name}. "
            if d.get("documents_folder") else "")


def _rig_once(model_path: str, kind: str = "", profile: str = "ue5", target_faces: int = 40000, max_faces: int = 0):
    """ONE rigging attempt. Returns a result dict, or an error sentence (str)."""
    src = Path(model_path).expanduser().resolve()
    if src.suffix.lower() not in MODEL_EXT + (".blend",) or not src.is_file():
        return "Give me an existing .glb, .gltf or .blend model file."
    if not any(src.is_relative_to(r) for r in _allowed_roots()):
        return "That file is outside the folders I may read models from (data, the Blender workspace, Downloads)."
    converted = None
    if src.suffix.lower() == ".blend":
        exe0 = blender_exe()
        if exe0 is None:
            return "Blender is not installed here (looked in Program Files and JARVIS_BLENDER_EXE)."
        work = Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace")) / "converted"
        work.mkdir(parents=True, exist_ok=True)
        glb = work / f"{re.sub(r'[^A-Za-z0-9_-]', '_', src.stem)[:40]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.glb"
        try:
            conv = craftjobs.run([str(exe0), "--background", "--factory-startup", "--python", str(HUB / "blend_to_glb.py"), "--",
                                   str(src), str(glb)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        except subprocess.TimeoutExpired:
            return "Reading that .blend file timed out after 15 minutes."
        if not glb.is_file():
            return "I could not read a mesh out of that .blend: " + (conv.stdout + conv.stderr)[-300:].replace("\r", " ")
        converted = {"from_blend": str(src), "converted_glb": str(glb)}
        src = glb
    if kind not in ("", "humanoid", "quadruped", "creature") or profile not in ("blender", "ue5") or not 2000 <= int(target_faces) <= 400000:
        return "Invalid kind, profile or target_faces (2000-400000)."
    exe = blender_exe()
    if exe is None:
        return "Blender is not installed here (looked in Program Files and JARVIS_BLENDER_EXE)."
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace")) / "rigged" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', src.stem)[:40]}-{stamp}"
    n = 1
    while True:                                   # two runs in one second (a retry) must never collide
        try:
            out.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            n += 1
            out = out.with_name(f"{out.name.split('--r')[0]}--r{n}")
    cmd = [str(exe), "--background", "--factory-startup", "--python", str(HUB / ("creature_acceptance.py" if kind == "creature" else "rig_acceptance.py")), "--",
           str(src), str(out), "--faces", str(int(target_faces)), "--profile", profile] + (["--kind", kind] if kind and kind != "creature" else []) \
        + (["--max-faces", str(int(max_faces))] if max_faces else [])
    variants = []
    try:                                           # what JARVIS has learned in practice: extra strategies, tried only if the default fit fails
        from core.dojo_store import Store
        variants = Store().runtime_variants()
        if variants:
            (out / "variants.json").write_text(json.dumps(variants), encoding="utf-8")
            cmd += ["--variants", str(out / "variants.json")]
    except Exception:
        variants = []
    t = time.time()
    craftjobs.step("rigging in Blender (skeleton, skin weights, pose tests, export)")
    try:
        proc = craftjobs.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    except subprocess.TimeoutExpired:
        return f"Rigging timed out after 25 minutes; partial files (if any) are in {out}. The model may be too dense; retry with fewer target_faces."
    report_file = out / "report.json"
    if not report_file.is_file():
        tail = (proc.stdout + proc.stderr)[-600:]
        return f"Rigging crashed before producing a report. Tail of Blender output: {tail}"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    stages = report["stages"]
    craftjobs.step("importing into Unreal to check it")
    unreal_gate = _unreal_gate(out, report) if profile == "ue5" else None
    if unreal_gate is not None:
        report["unreal_gate"] = unreal_gate
        if not unreal_gate.get("skipped"):     # skipped by the Mark V guard = not checked, like no Unreal at all
            stages["unreal_import"] = "PASS" if unreal_gate.get("ok") else "FAIL"
        report_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    visual = None
    if kind == "creature" and os.getenv("JARVIS_SKIP_VISION") != "1":
        craftjobs.step("looking at the rig with my vision model")
        try:
            from core import dojo_vision
            visual = dojo_vision.review(out, (report.get("pipeline") or {}).get("anatomy"))
        except Exception:
            visual = None
    ok = all(_stage_ok(k, v) for k, v in stages.items())
    chosen = str((report.get("pipeline") or {}).get("chosen_strategy", ""))
    if chosen.startswith("learned:"):
        try:
            from core.dojo_store import Store
            Store().record_variant_use(chosen.split(":", 1)[1], ok)
        except Exception:
            pass
    led = record_run(report, src, out, ok)
    write_manifest(out, src, report, ok, round(time.time() - t, 1))
    try:                                  # the owner's standing rule: every Meshy model is his (core/assetrights)
        from core import assetrights
        assetrights.record(out, str(model_path), _sha(src))
    except Exception:  # noqa: BLE001  (a missing record only leaves the provenance gate open; it never fails the rig)
        pass
    p = report["pipeline"]
    chosen = p.get("chosen_strategy", "fit")
    return ({**(converted or {}), "ok": ok, "kind": p["kind_used"], "bones": p["bones"], "seconds": round(time.time() - t, 1),
        "skin_method": p["skin"]["final_method"], "layout_strategy": chosen, "stages": stages,
        "failing_pose_tests": [x["test"] for x in (report["pose_tests"] + report.get("motion_tests", [])) if not x["pass"]],
        "anatomy": (p.get("anatomy") if kind == "creature" else None), "visual_review": visual,
        "pruned_bones": [b if isinstance(b, str) else str(b) for b in (p.get("pruned_bones") or [])],
        "output_folder": str(out), "files": sorted(f.name for f in out.iterdir() if f.suffix in (".glb", ".fbx", ".json", ".png")),
        "unreal_gate": unreal_gate,
        "note": "Pass/fail above is measured in real Blender" + (" and a real headless Unreal import." if unreal_gate and not unreal_gate.get("skipped")
                                                            else ". Unreal was not checked."),
        "lessons_known": len(led["lessons"])})


HUNYUAN_NOTE = ("Licence note: this shape was generated with Hunyuan3D, whose licence does not cover the EU, UK or South Korea "
                "and forbids using its output to train other AI. Your own art and the rig are yours; details are in provenance.json.")


def unreal_sentence(d):
    g = d.get("unreal_gate")
    if g is None:
        return "Unreal was not available, so the Unreal import is not verified."
    if g.get("skipped"):
        return f"I didn't run the Unreal import check because {g['skipped']}, so the Unreal import is not verified yet."
    if g.get("ok"):
        return f"I also imported it into Unreal headlessly: one root bone, {g['bones']} bones, skin weights normalised, animation plays."
    return "The Unreal import check FAILED: " + str(g.get("error") or g.get("error_run") or "root bone, vertex count, animation or skin weights were wrong") + "."


def _speak_creature(d, what):
    an, st = d["anatomy"], d["stages"]
    c = an.get("counts", {})
    parts = [f"{n} {w}" for n, w in ((an.get("legs", 0), "legs"), (an.get("wing_parts", 0), "wings"), (c.get("arm", 0), "arms"),
                                      (c.get("head_end", 0), "head"), (c.get("tail_end", 0), "tail")) if n]
    text = (f"I worked out its body from its shape alone, with no template: {', '.join(parts) or 'a single body'}, "
            f"{an.get('bones', d['bones'])} bones. Movements I tested: {', '.join(an.get('movement_modes', []))}. ")
    if d["ok"]:
        text += (f"Everything I measured passed: {st['pose_tests'].replace(' passed', '')} joint tests, {st['motion_tests'].replace(' passed', '')} movement tests, "
                 "export and re-import with animation. ")
    else:
        bad = [k for k, v in st.items() if not _stage_ok(k, v)]
        text += "Some checks failed: " + ", ".join(bad) + (f" (for example {', '.join(d['failing_pose_tests'][:4])})" if d.get("failing_pose_tests") else "") + ". "
    v = d.get("visual_review")
    if v and "error" not in v:
        if v.get("disagrees_on"):
            text += f"My vision check, which is only a second opinion, thinks {v.get('creature_guess', 'it')} and disagrees about {', '.join(v['disagrees_on'])}. "
        else:
            text += f"My vision check, only a second opinion, sees a {v.get('creature_guess', 'creature')} and agrees with my count. "
    return text + f"Files are in {d['output_folder']}. " + _docs_sentence(d) + unreal_sentence(d) + (" " + d["license_note"] if d.get("license_note") else "")


def _speak(d):
    """A rig result as plain sentences (the tools are direct: a small local model must not re-phrase JSON)."""
    st = d["stages"]
    what = Path(d.get("from_blend") or d.get("generated_shape") or d["output_folder"]).stem
    head = (f"Rigged {what}: {d['kind']}, {d['bones']} bones, skinned with {d['skin_method'].replace('_', ' ')}. ")
    if d.get("anatomy"):
        return head + _speak_creature(d, what)
    if d["ok"]:
        body = (f"Everything I measured passed: skin weights, {st['pose_tests'].replace(' passed', '')} joint deformation tests, "
                f"{st['ik_fk'].replace(' passed', '')} IK/FK tests, export, and re-import with animation. ")
    else:
        bad = [k for k, v in st.items() if not _stage_ok(k, v)]
        body = ("Some checks failed: " + ", ".join(bad) + (f" (failing joints: {', '.join(d['failing_pose_tests'])})" if d["failing_pose_tests"] else "") + ". ")
    extra = f"The 3D shape took {d['image_to_3d_seconds']} seconds to generate. " if d.get("image_to_3d_seconds") else ""
    pruned = d.get("pruned_bones") or []
    if pruned:
        extra += (f"I removed {len(pruned)} finger or toe bones the mesh has no separate geometry for (gloves, boots or claws fused "
                  "into one shape), so those parts move as a block. ")
    tried = d.get("attempts") or []
    if len(tried) > 1:
        from tools import remedies as R
        steps = "; ".join(f"attempt {a['n']} failed on {a['failure']}, so I {R.REMEDIES[a['next_remedy']]}" for a in tried if a.get("next_remedy"))
        extra += f"It took {len(tried)} attempts: {steps}. "
    return (head + extra + body + f"Files are in {d['output_folder']}: a .glb for Blender and an .fbx with Unreal mannequin bone names. "
            + _docs_sentence(d) + unreal_sentence(d) + (" " + d["license_note"] if d.get("license_note") else ""))


@tool("Rig a 3D character/creature model (GLB/GLTF) for games, entirely by yourself and headless: cleans the mesh, "
      "detects humanoid vs quadruped, builds a fitted skeleton (fingers/IK for humanoids), skins it, REPAIRS bad "
      "weights, then MEASURES shoulder/elbow/wrist/finger/hip/knee/ankle deformation, IK/FK, animation, "
      "export and re-import. Produces .glb and .fbx (profile ue5 = Unreal bone names). Free, local, never overwrites. "
      "Use for 'rig this model', 'make this game-ready'.",
      {"model_path": {"type": "string"}, "kind": {"type": "string", "enum": ["", "humanoid", "quadruped"]},
       "profile": {"type": "string", "enum": ["blender", "ue5"]}, "target_faces": {"type": "integer"}},
      required=["model_path"], slow=True, direct=True)
def rig_character(model_path: str, kind: str = "", profile: str = "ue5", target_faces: int = 40000) -> str:
    r = _rig(model_path, kind, profile, target_faces)
    return r if isinstance(r, str) else _speak(r)


def spoken_status(status):
    """The status as plain sentences, so a small local model cannot garble it (the tool is direct)."""
    names = {"skin_valid": "skinning", "pose_tests": "joint deformation tests", "ik_fk": "IK and FK controls",
             "export": "exporting GLB and FBX", "reimport_animation_roundtrip": "re-importing with animation", "fingers": "finger deformation"}
    ver = [n for k, n in names.items() if status[k]["state"] == "verified"]
    bad = [n for k, n in names.items() if status[k]["state"] == "failed"]
    non = [n for k, n in names.items() if status[k]["state"] == "untested"]
    runs = max([status[k]["passed_runs"] + status[k]["failed_runs"] for k in names] or [0])
    kinds = sorted({x for k in names for x in status[k]["kinds"]})
    parts = []
    if not runs:
        parts.append("I have not rigged anything on this PC yet, so I can't claim any rigging skill; every one is untested.")
    else:
        parts.append(f"I can rig a 3D character myself. Measured in real Blender over {runs} run{'s' if runs != 1 else ''}"
                     + (f" on {' and '.join(kinds)} models" if kinds else "") + f", these are verified: {', '.join(ver) or 'none yet'}.")
    if bad:
        parts.append("These failed: " + ", ".join(bad) + ".")
    if non:
        parts.append("Still untested: " + ", ".join(non) + ".")
    parts.append("Not covered by any test yet: importing through the Unreal editor UI, and true retopology (the mesh is only reduced by decimation, not remodelled). "
                 "I don't call any of this mastered. Give me an image or a GLB and I will rig it.")
    return " ".join(parts)


@tool("Answer 'what are your rigging skills / Blender skills / can you rig characters / how good are you at rigging': "
      "what I can honestly do with character rigging, per capability, from real test-run evidence (verified, failed, untested). "
      "Use before promising a rigging result.", {}, direct=True)
def rigging_skills() -> str:
    return spoken_status(skills_status())


@tool("Answer 'what have you learned about rigging / from your rigging tests': lessons I recorded from past real "
      "rigging runs (what failed, what repaired it, density and fit rules). Consult before rigging a tricky model.",
      {"limit": {"type": "integer"}}, direct=True)
def rigging_lessons(limit: int = 12) -> str:
    led = _ledger()
    items = [l["text"] for l in led["lessons"][-max(1, min(int(limit), 40)):]]
    if not items:
        return "I haven't recorded any rigging lessons yet; they come from real rigging runs."
    return f"From {len(led['runs'])} real rigging runs I learned: " + " ".join(f"{i + 1}. {x}" for i, x in enumerate(items))


def hunyuan_paths():
    """Isolated Hunyuan3D install: (python, repo, hf_home) or None. Never the JARVIS/Ollama environment."""
    env = os.getenv("JARVIS_HUNYUAN_HOME", "")
    homes = [Path(env)] if env else []
    local = Path(os.getenv("LOCALAPPDATA", ""))
    homes += [local / "Jarvis3D", *sorted((local / "Packages").glob("OpenAI.Codex_*/LocalCache/Local/Jarvis3D"))]
    for h in homes:
        py = h / "hunyuan-venv" / "Scripts" / "python.exe"
        if py.is_file() and (h / "Hunyuan3D-2").is_dir():
            return py, h / "Hunyuan3D-2", h / "hf-cache"
    return None


@tool("Turn ONE image of a character, creature or robot into a fully rigged, game-ready 3D model by yourself: "
      "runs the local GPU image-to-3D model (Hunyuan3D, free, offline), then rigs it with rig_character "
      "(skeleton, IK, validated skinning, measured tests, GLB+FBX for Blender/Unreal). Takes a few minutes. "
      "Use for 'use this image to create a fully rigged game character'.",
      {"image_path": {"type": "string"}, "kind": {"type": "string", "enum": ["", "humanoid", "quadruped"]},
       "profile": {"type": "string", "enum": ["blender", "ue5"]}}, required=["image_path"], slow=True, direct=True)
def image_to_character(image_path: str, kind: str = "", profile: str = "ue5") -> str:
    img = Path(image_path).expanduser().resolve()
    if img.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not img.is_file():
        return "Give me an existing PNG, JPG or WEBP image."
    if not any(img.is_relative_to(r) for r in _allowed_roots()):
        return "That image is outside the folders I may read from (data, the Blender workspace, Downloads)."
    hp = hunyuan_paths()
    if hp is None:
        return "The isolated Hunyuan3D install was not found (set JARVIS_HUNYUAN_HOME)."
    py, repo, hf = hp
    gpu = None
    try:
        from tools import pcops
        gpu = pcops.gpu()
    except Exception:
        pass
    if gpu and gpu["free_mb"] < 6000:
        return f"The GPU only has {gpu['free_mb']} MB free (a game or model is using it); I need about 6 GB. Close it and ask again."
    out = Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace")) / "generated" / \
        f"{re.sub(r'[^A-Za-z0-9_-]', '_', img.stem)[:40]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=False)
    glb = out / "shape.glb"
    t = time.time()
    try:
        proc = craftjobs.run([str(py), str(ROOT / "scripts" / "image_to_glb.py"), str(img), str(glb), "--repo", str(repo),
                               "--hf-home", str(hf)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200, cwd=str(out))
    except subprocess.TimeoutExpired:
        return "Image-to-3D timed out after 20 minutes."
    if not glb.is_file():
        return "Image-to-3D failed: " + (proc.stderr or proc.stdout)[-500:].replace("\r", " ")
    gen_s = round(time.time() - t, 1)
    result = _rig(str(glb), kind, profile)
    if isinstance(result, str):
        return f"3D shape generated ({gen_s}s) at {glb}, but rigging reported: {result}"
    result["image_to_3d_seconds"] = gen_s
    result["generated_shape"] = str(glb)
    result["license_note"] = HUNYUAN_NOTE
    try:
        (out / "provenance.json").write_text(json.dumps({
            "generator": "Hunyuan3D-2 (tencent/Hunyuan3D-2), run locally", "license": "Tencent Hunyuan 3D 2.0 Community License",
            "restrictions": ["Territory excludes the EU, UK and South Korea (use/distribution of Output outside it is unlicensed)",
                             "More than 1M monthly active users needs a Tencent licence",
                             "Output may not be used to train or improve other AI models"],
            "input_image": str(img), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "audit": "docs/rigging/LICENSES.md"}, indent=2), encoding="utf-8")
    except OSError:
        pass
    return _speak(result)
