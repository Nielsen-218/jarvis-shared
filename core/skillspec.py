"""The reusable SKILL representation: what a learned ability is, beyond its code.

    {
      "name":          "close_site_tab",                      the skill (one per contract)
      "goal":          plain task statement,
      "inputs":        {"site": "short site name, e.g. github"},
      "preconditions": ["a browser window is open", ...]      checked by the procedure / its capabilities
      "dependencies":  {"capabilities": ["browser", "util"],  DEVELOPER-BUILT primitives (core/capabilities)
                        "skills": []},                        other learned skills it builds on (future)
      "steps":         ["list every tab", ...]                the plan in plain words (written by JARVIS)
      "expected":      ["no tab of the site remains", ...]    what verify() checks
      "recovery":      ["if a tab vanished, re-list", ...]    what recover() does
      "needs":         []                                     capabilities the plan would need that do not exist (tool requests)
      "verification":  {"independent": "...", "sealed_exam": 12}   how the HOST proves it (never the procedure itself)
    }
    + procedure source: def run(<caps>, params) / def verify(...) / def recover(..., error)   (core/methodlang.check_procedure)

Retained skills live in their own library (JARVIS_SKILLS_DIR, default data/learned-skills) through core/procedures.Library,
which refuses anything without 12/12 independent-judge evidence and re-checks the source hash on every load. Labels:
  "procedure_by": "jarvis_learned" | "teacher_assisted"   (never "independent" once any lesson was used)
  "primitives_by": "developer"                            (every capability it depends on)
"""
import os
from pathlib import Path

FIELDS = ("name", "goal", "inputs", "preconditions", "dependencies", "steps", "expected", "recovery", "needs", "verification")


class BadSpec(ValueError):
    pass


def _strs(v, n=12):
    if isinstance(v, str):
        v = [v]
    return [str(x)[:300] for x in (v or []) if str(x).strip()][:n]


def normalise(raw: dict, contract: dict, source: str, granted=None) -> dict:
    """The learner's spec, made exact by the host: goal/inputs/verification come from the contract, dependencies must be
    usable capabilities and must cover what the procedure really calls. `granted` = the capabilities the host actually handed
    the procedure (e.g. util is always added): those count as declared (live 2026-11-08: half the candidates were refused
    for calling util, which the host had given them itself)."""
    from core import capabilities, methodlang
    if not isinstance(raw, dict):
        raise BadSpec("the skill description must be a JSON object")
    deps = raw.get("dependencies") or {}
    caps = deps.get("capabilities") if isinstance(deps, dict) else deps
    caps = [str(c) for c in (caps or []) if str(c)]
    caps += [c for c in (granted or []) if c not in caps]
    used = methodlang.capabilities_used(source)
    missing = sorted(used - set(caps))
    if missing:
        raise BadSpec(f"the procedure calls {', '.join(missing)} but its dependencies do not list it")
    unusable = [c for c in caps if c not in capabilities.usable()]
    needs = _strs(raw.get("needs")) + [f"capability {c} (planned, not built)" for c in unusable]
    caps = [c for c in caps if c in capabilities.usable()]
    if not caps:
        raise BadSpec("no usable capability in dependencies")
    return {"name": contract["name"], "goal": contract["goal"], "inputs": contract["inputs"],
            "preconditions": _strs(raw.get("preconditions")), "dependencies": {"capabilities": caps, "skills": _strs(deps.get("skills")) if isinstance(deps, dict) else []},
            "steps": _strs(raw.get("steps"), 20), "expected": _strs(raw.get("expected")), "recovery": _strs(raw.get("recovery")),
            "needs": needs, "verification": contract["verification"]}


def impls(caps, bridge=None) -> dict:
    out = {}
    if "browser" in caps:
        from core import browserbridge
        if bridge is None:
            raise RuntimeError("the browser capability needs the tab bridge (lab only for now)")
        out["browser"] = browserbridge.BrowserAPI(bridge)
    if "ui" in caps:
        from core import methodlang
        out["ui"] = methodlang.UIA()
    return out


def run_in_lab(source, caps, params, bridge) -> dict:
    from core import methodlang
    if os.getenv("JARVIS_SKILL_LAB") != "1":
        raise RuntimeError("lab only")
    return methodlang.run_procedure(source, impls(caps, bridge), list(caps), params)


# ------------------------------------------------------------------------------------------------ retained skills

def _root(store=None) -> Path:
    from core import storage
    return Path(store or os.getenv("JARVIS_SKILLS_DIR", str(storage.DATA / "learned-skills")))


def library(store=None):
    from core import procedures
    return procedures.Library(_root(store))


def retain(spec, source, evidence, provenance, learner_model, extra=None, store=None) -> dict:
    """Register a skill whose procedure passed every sealed world. procedures.Library refuses anything less than 12/12."""
    by = "teacher_assisted" if provenance.get("provenance") == "teacher_assisted" else "jarvis_learned"
    return library(store).register(
        f"skill.{spec['name']}", f"Learned skill: {spec['name']}", source, triggers=[[spec["name"]]],
        params={k: {"type": "string"} for k in spec["inputs"]}, recovery={"steps": spec["recovery"]},
        authored_by=f"local:{learner_model}", evidence=evidence, entry="run", scope=spec["goal"],
        extra={"spec": spec, "language": "core.methodlang.procedure", "status": "verified",
               "procedure_by": by, "primitives_by": "developer",
               "primitives": {c: "developer-built (core/capabilities)" for c in spec["dependencies"]["capabilities"]},
               **provenance, **(extra or {})})


# ------------------------------------------------------------------------------------------------ production (owner-approved only)

def _approvals(store=None) -> Path:
    return _root(store) / "approvals.json"


def approve(skill_id, version=None, by="owner", store=None, exam=None) -> dict:
    """Bound to the exact source hash: any change needs a new approval. by="owner" (scripts/skills.py approve) or by="auto"
    with the exam evidence (core/learned.auto_approve, owner decision 2026-11-12)."""
    import json
    import time
    man, _ = library(store).get(skill_id, version)
    rows = json.loads(_approvals(store).read_text(encoding="utf-8")) if _approvals(store).exists() else {}
    rows[skill_id] = {"version": man["version"], "source_sha": man["source_sha"], "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      **({"exam": exam} if exam else {})}
    _approvals(store).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows[skill_id]


def revoke(skill_id, store=None) -> None:
    import json
    rows = json.loads(_approvals(store).read_text(encoding="utf-8")) if _approvals(store).exists() else {}
    rows.pop(skill_id, None)
    _approvals(store).write_text(json.dumps(rows, indent=1), encoding="utf-8")


def approved_skill(skill_id, store=None):
    """-> (manifest, source) of the ACTIVE version when the owner approved exactly that code, else None."""
    import json
    try:
        rows = json.loads(_approvals(store).read_text(encoding="utf-8")) if _approvals(store).exists() else {}
        got = library(store).get(skill_id)
    except Exception:  # noqa: BLE001 - a broken store never breaks the built-in methods
        return None
    row = rows.get(skill_id) or {}
    if not got or row.get("source_sha") != got[0]["source_sha"] or row.get("version") != got[0]["version"]:
        return None
    return got


def candidates_for(tool, store=None) -> list:
    """Extra methods for core/pcmethods: an approved learned skill for exactly this step, whose primitives are live here."""
    if os.getenv("JARVIS_LEARNED_METHODS", "on").lower() == "off":
        return []
    got = approved_skill(f"skill.{tool}", store)
    if not got:
        return []
    if "browser" in got[0]["spec"]["dependencies"]["capabilities"]:
        from core import browserbridge
        b = browserbridge.live()
        if not (b and b.connected()):
            return []                                # the extension is not installed / not connected: the skill cannot run
    return [f"skill:skill.{tool}"]


def run_approved(method, step, store=None) -> dict:
    """Run an approved skill on the owner's desktop, then check the result INDEPENDENTLY (UI Automation tab names, not the
    extension the procedure used), like core/acquire.run_learned does for learned methods."""
    import time as _t
    from core import acquire, browserbridge, methodlang, skillcontracts
    sid = method.split(":", 1)[1]
    got = approved_skill(sid, store)
    if not got:
        raise RuntimeError("That learned skill has not been approved by the owner.")
    man, src = got
    caps = man["spec"]["dependencies"]["capabilities"]
    impl = {}
    if "browser" in caps:
        b = browserbridge.live()
        if not (b and b.connected()):
            raise RuntimeError("The JARVIS tab extension is not connected, so my learned skill cannot run.")
        impl["browser"] = browserbridge.BrowserAPI(b)
    if "ui" in caps:
        impl["ui"] = methodlang.UIA()
    before, wb = acquire._tab_names()
    result = methodlang.run_procedure(src, impl, caps, step["arguments"])
    _t.sleep(.6)
    after, wa = acquire._tab_names()
    check = skillcontracts.production_check(step["tool"], step["arguments"], before, after, wb, wa, result)
    from core import independence
    library(store).log(id=sid, version=man["version"], tool=step["tool"], args=step["arguments"], passed=check["passed"],
                       checks=check["checks"], error=result.get("error", ""), self_check=result.get("self_check"),
                       source=independence.asker(), teacher=False, outside_calls=0)
    independence.log_use(sid, "skill", check["passed"], tool=step["tool"])     # ran from disk: no teacher, no outside call
    if not check["passed"]:
        if not result.get("ok"):
            raise RuntimeError("My learned skill could not do it either; nothing else was changed.")
        return {"verified": False, "detail": "My learned skill ran, but I could not confirm the tab closed."}
    site = skillcontracts._SHOWN[step["arguments"]["site"]]
    n = len(check["closed"])
    return {"verified": True, "detail": f"Closed the {site} tab{'s' if n > 1 else ''}.", "learned_skill": f"{sid} v{man['version']}"}


def run_stored(skill_id, params, bridge, store=None, version=None) -> dict:
    """A FRESH process loads a retained skill from disk (hash-checked) and runs it: the persistence test. `version` pins the
    exact version that was just retained (live 2026-11-08: an id-wide DISABLED flag hid the new version from the test)."""
    from core import methodlang
    if os.getenv("JARVIS_SKILL_LAB") != "1":
        raise RuntimeError("lab only")
    got = library(store).get(skill_id, version)
    if not got:
        return {"ok": False, "error": f"no stored skill {skill_id}", "error_type": "Missing", "message": ""}
    man, src = got
    caps = man["spec"]["dependencies"]["capabilities"]
    out = methodlang.run_procedure(src, impls(caps, bridge), caps, params)
    out["loaded"] = {"id": man["id"], "version": man["version"], "source_sha": man["source_sha"]}
    return out
