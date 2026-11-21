"""Failure taxonomy -> remedies -> learned ranking. Pure logic (no Blender), so it is unit-tested directly.

A rig attempt that fails is classified into a failure CODE; each code has candidate REMEDIES (a change of kind or mesh
density). The next remedy tried is the untried one with the best recorded win rate for that code, so JARVIS gets
better at recovering with every run. Budget: MAX_ATTEMPTS per job."""

MAX_ATTEMPTS = 3
MIN_FACES, MAX_FACES = 8000, 300000

REMEDIES = {
    "halve_faces": "use a simpler game mesh (half the faces)",
    "double_faces": "use a denser game mesh (twice the faces)",
    "swap_kind": "treat the creature as the other body type",
}

TAXONOMY = {
    "timeout": ["halve_faces"],
    "crash": ["halve_faces", "swap_kind"],
    "skin_invalid": ["halve_faces"],
    # swap_kind is NOT offered for pose failures: the corpus run showed it retrying a plainly humanoid character as a
    # quadruped (no chance of helping). It stays for crashes, where the body type itself may be what broke.
    "pose_limbs": ["double_faces", "halve_faces"],
    "pose_fingers": ["double_faces"],
    "ik": ["halve_faces"],
    "export": ["halve_faces"],
    "roundtrip": ["halve_faces", "double_faces"],
    "unreal": [],
}


def stage_ok(v):
    """A stage value is good when it is True, "PASS", or "n/n passed"."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and "/" in v:
        a, b = v.split()[0].split("/")
        return a == b and int(b) > 0
    return v == "PASS"


def classify(result):
    """Failure code for a rig attempt (dict from _rig_once or its error string); None = success or not retryable."""
    if isinstance(result, str):
        low = result.lower()
        if "timed out" in low:
            return "timeout"
        if "crashed before producing a report" in low:
            return "crash"
        return None
    if result.get("ok"):
        return None
    st = result.get("stages", {})
    if st.get("skin_valid") is False:
        return "skin_invalid"
    failing = result.get("failing_pose_tests") or []
    if failing:
        return "pose_fingers" if all("finger" in f or "toe" in f for f in failing) else "pose_limbs"
    for stage, code in (("ik_fk", "ik"), ("export", "export"), ("reimport_animation_roundtrip", "roundtrip"), ("unreal_import", "unreal")):
        if stage in st and not stage_ok(st[stage]):
            return code
    return None


def apply(remedy, kind, faces):
    """New (kind, faces) after a remedy, or None when it changes nothing."""
    if remedy == "halve_faces":
        new = (kind, max(MIN_FACES, faces // 2))
    elif remedy == "double_faces":
        new = (kind, min(MAX_FACES, faces * 2))
    elif remedy == "swap_kind":
        new = ("quadruped" if kind != "quadruped" else "humanoid", faces)
    else:
        return None
    return new if new != (kind, faces) else None


def rank(code, stats, tried):
    """Remedies for `code` not yet tried, best learned win rate first (Laplace-smoothed; ties keep table order)."""
    cand = [r for r in TAXONOMY.get(code, []) if r not in tried]
    def score(r):
        s = (stats.get(code) or {}).get(r) or {}
        return -((s.get("wins", 0) + 1) / (s.get("tries", 0) + 2))
    return sorted(cand, key=score)


def record(stats, code, remedy, won):
    s = stats.setdefault(code, {}).setdefault(remedy, {"tries": 0, "wins": 0})
    s["tries"] += 1
    s["wins"] += 1 if won else 0
    return stats


def next_step(code, kind, faces, stats, tried_params, tried_remedies):
    """(remedy, kind, faces) to try next, or None when the budget/ideas are exhausted."""
    for r in rank(code, stats, tried_remedies):
        new = apply(r, kind, faces)
        if new and new not in tried_params:
            return r, new[0], new[1]
    return None


def score(result):
    """Comparable quality of an attempt, so the best one is kept when none fully passes."""
    if isinstance(result, str):
        return (-1, 0)
    st = result.get("stages", {})
    passed = sum(1 for v in st.values() if stage_ok(v))
    return (passed, -len(result.get("failing_pose_tests") or []))
