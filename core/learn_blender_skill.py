"""Blender basics WITH procedural memory: the same task, judge, tasks and store as core/learn_blender_basics.py (this subclass changes none of
them, so stored episodes and the judge hash are shared) plus two actions:

  use_skill   find the stored verified procedure that applies to the task text, supply its parameters, run it, and recover from named errors
  auto        use_skill when a procedure applies, else fall back to llm_plain (what JARVIS would do with no stored knowledge)

The procedure is Claude-authored and judge-verified (see procedures/ and the manifest evidence): what JARVIS does here is RECOGNISE the task,
SUPPLY the parameters, EXECUTE, DETECT failure and CHOOSE a recovery. The judge remains the only authority on the outcome."""
import time

from core import learn_blender_basics as bb
from core import procedures
from core.config import ROOT

PID = "blender.two_bone_limb_bind"
SOURCE = ROOT / "procedures" / "blender_two_bone_limb_bind.py"

TRIGGERS = {"all": [["armature", "rig"], ["two bones", "two-bone", "exactly two"], ["bind", "automatic weights", "skin"]],
            "exclude": ["three", "four", "tail", "spine", "humanoid", "creature", "character", "dragon", "quadruped", "legs", "fingers"]}
PARAMS = {"mesh_name": {"type": "string", "from_text": r"mesh object named '([^']+)'"},
          "rig_name": {"type": "string", "from_text": r"armature object named '([^']+)'"},
          "bone_names": {"type": "strings", "from_text": r"bones named '([^']+)' and '([^']+)'"},
          "axis": {"type": "string", "enum": ["x", "y", "z"], "from_context": "axis"},
          "start": {"type": "number", "from_context": "start"},
          "end": {"type": "number", "from_context": "end"},
          "bands": {"type": "numbers", "default": [0.15, 0.10, 0.05]},
          "if_exists": {"type": "string", "enum": ["error", "rename_old"], "default": "error"}}
RECOVERY = {"rig_exists": {"set": {"if_exists": "rename_old"}, "why": "keep the old object under another name and build the new rig"},
            "verification_failed": {"set": {"bands": [0.12, 0.08, 0.03], "if_exists": "rename_old"}, "why": "retry with narrower blend widths"},
            "mesh_missing": {"ask_user": True, "why": "there is no such mesh to bind"},
            "bad_axis": {"ask_user": True, "why": "the tube must lie along x, y or z"},
            "bad_range": {"ask_user": True, "why": "end must be greater than start"},
            "too_thick": {"ask_user": True, "why": "tubes thicker than 0.135 x length tear with this weighting: out of the verified range"},
            "mesh_too_small": {"ask_user": True, "why": "the mesh is too small to bind"}}
SCOPE = "a straight, axis-aligned tube (radius <= 0.135 x length) bound to exactly two bones; NOT characters, creatures, tails, more bones or bent meshes"


def library(store):
    return procedures.Library(store.root / "procedures")


def register_from_evidence(store, evidence, authored_by="Claude (teacher-assisted); verified by the independent judge"):
    return library(store).register(PID, "Bind a straight tube to a two-bone armature and verify the bend", SOURCE.read_text(encoding="utf-8"),
                                   TRIGGERS, PARAMS, RECOVERY, authored_by, evidence, entry="run", scope=SCOPE)


class BlenderSkillAdapter(bb.BlenderBasicsAdapter):
    def actions(self):
        return ["noop", "llm_plain", "llm_docs", "use_skill", "auto"]

    def default_action(self):
        return "auto"

    def procedure_for(self, task, store, text=None):
        lib = library(store)
        m = lib.match(text or bb.TASK_TEXT, context=task["variant"])
        return lib, m

    def run_procedure(self, task, store, max_recoveries=1, text=None):
        """-> (verdict, trail). Match, render, run in the judge, then at most `max_recoveries` named recoveries.
        `text` is the task wording the procedure is matched on (the training wording unless a live request supplies its own)."""
        lib, m = self.procedure_for(task, store, text)
        trail = []
        if not m:
            lib.log(task=task["id"], matched=None)
            return None, trail
        params, man = dict(m["params"]), m["manifest"]
        lib.log(task=task["id"], matched=m["id"], version=man["version"], params=params)
        for attempt in range(1 + max_recoveries):
            verdict = bb.judge(task["variant"], lib.render(m["id"], params, man["version"]))
            ok = verdict["full"]
            trail.append({"attempt": attempt, "full": ok, "error": verdict.get("error")})
            lib.log(task=task["id"], procedure=m["id"], attempt=attempt, full=ok, error=verdict.get("error"))
            if ok:
                break
            rec = lib.recovery_for(man, verdict.get("error"))
            if not rec or rec.get("ask_user") or attempt == max_recoveries:
                trail.append({"recovery": rec})
                break
            params.update(rec["set"])
            trail.append({"recovery": rec["class"], "set": rec["set"]})
        return verdict, trail

    def execute(self, task, action, ctx):
        if action not in ("use_skill", "auto"):
            return super().execute(task, action, ctx)
        t0 = time.monotonic()
        verdict, trail = self.run_procedure(task, ctx["store"])
        if verdict is None:
            if action == "use_skill":
                return {"full": False, "stages_ok": 0, "fail_count": len(bb.CHECKS), "total": len(bb.CHECKS), "failing": list(bb.CHECKS),
                        "cost_s": round(time.monotonic() - t0, 1), "hints": [], "error": "no stored procedure applies"}
            return super().execute(task, "llm_plain", ctx)         # nothing stored applies: regenerate, as before
        # the procedure was written by the teacher: its episodes are marked assisted, so they never count as unaided evidence
        return {**verdict, "cost_s": round(time.monotonic() - t0, 1), "hints": ["teacher", f"procedure:{PID}"], "trail": trail}


# ---- live requests: the owner's own words -> this skill (used by tools/blenderskills.py and the chat front door) -------------------------------

import re  # noqa: E402

_NUM = r"(-?\d+(?:\.\d+)?)"
_NAME = r"['\"]?([A-Za-z0-9_.-]{1,40})['\"]?"
_OPEN_SCENE = re.compile(r"\b(?:i have open|open in blender|my scene|my blender|selected|current scene|already open|the one i made|my own|my mesh|my tube|my limb)\b", re.I)
_NOT_STRAIGHT = re.compile(r"\b(?:bent|curved|curve|diagonal|slanted|twisted|sphere|cube|plane|torus|cone)\b", re.I)


def gate(text):
    """Cheap, pure pre-filter for the chat front door: does this sound like the two-bone bind job at all? (No disk, no Blender.)"""
    t = text or ""
    return bool(re.search(r"\b(?:two|2)[- ]bones?\b|\bexactly two\b|\bupper and lower\b", t, re.I)
                and re.search(r"\b(?:bind|skin|automatic weights|weights?)\b", t, re.I) and re.search(r"\b(?:armature|rig)\b", t, re.I)
                and not re.search(r"\b(?:how (?:do|can|would|to)|tutorials?|explain|what is)\b", t, re.I))


def understand(text):
    """The owner's request -> {variant, canonical, assumed, decline}. Pure parsing; `canonical` is the task wording the stored procedure matches on.
    Anything not stated is defaulted AND reported in `assumed`, never silently."""
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", text or "").strip()
    out = {"variant": None, "canonical": None, "assumed": [], "decline": None}
    if _OPEN_SCENE.search(t):
        out["decline"] = ("I can do that on a tube I build myself, where I can measure the bend independently. I can't verify a bind on the mesh in your open scene yet, "
                          "so I won't touch it. Describe the tube (axis, start and end, radius) and I'll build and check one.")
        return out
    if _NOT_STRAIGHT.search(t):
        out["decline"] = "My verified skill covers a straight, axis-aligned tube bound to exactly two bones. That shape is outside it, so I won't guess."
        return out
    pick = lambda pat, d: (re.search(pat, t, re.I).group(1) if re.search(pat, t, re.I) else d)
    mesh = pick(r"(?:tube|mesh|cylinder|limb|object)\s+(?:object\s+)?(?:named|called)\s+" + _NAME, "Limb")
    rig = pick(r"(?:armature|rig)\s+(?:object\s+)?(?:named|called)\s+" + _NAME, "Rig")
    bm = re.search(r"bones?\s+(?:named|called)\s+" + _NAME + r"\s+and\s+" + _NAME, t, re.I)
    up, lo = (bm.group(1), bm.group(2)) if bm else ("upper", "lower")
    am = re.search(r"\b([xyz])[- ]axis\b|\balong\s+(?:the\s+)?([xyz])\b", t, re.I)
    axis = (am.group(1) or am.group(2)).lower() if am else "z"
    if not am:
        out["assumed"].append("the z axis")
    rm = re.search(r"from\s+" + _NUM + r"\s*(?:to|until|through|-)\s*" + _NUM, t, re.I)
    lm = re.search(_NUM + r"\s*(?:m|meters?|metres?|units?)?\s*(?:long|tall|in length)", t, re.I)
    if rm:
        start, end = float(rm.group(1)), float(rm.group(2))
    elif lm:
        start, end = 0.0, float(lm.group(1))
    else:
        start, end = 0.0, 1.0
        out["assumed"].append("a tube from 0 to 1")
    length = end - start
    radm = re.search(r"radius\s*(?:of|=|is)?\s*" + _NUM, t, re.I)
    if radm:
        radius = float(radm.group(1))
    else:
        radius = round(min(0.08, 0.1 * length), 3) if length > 0 else 0.08
        out["assumed"].append(f"radius {radius}")
    out["variant"] = {"axis": axis, "start": start, "end": end, "radius": radius}
    # The independent judge looks for exactly Limb / Rig / upper / lower, so those are the only names whose result it can verify.
    if (mesh, rig, up, lo) != ("Limb", "Rig", "upper", "lower"):
        out["assumed"].append(f"the names Limb, Rig, upper and lower instead of {mesh}, {rig}, {up} and {lo}, because my independent check can only verify those")
    out["canonical"] = ("A mesh object named 'Limb' (a straight tube) is in the scene. Create an armature object named 'Rig' with exactly two bones named "
                        "'upper' and 'lower' and bind the mesh to it with automatic weights so that bending the lower bone bends the tube.")
    out["names"] = {"mesh": "Limb", "rig": "Rig", "bones": ["upper", "lower"]}
    return out
