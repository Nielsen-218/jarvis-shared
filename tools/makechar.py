"""Describe a character in words -> concept image (FLUX.2 klein, local) in a rig-friendly pose -> checked by the local
vision model -> 3D shape (Hunyuan3D, isolated venv) -> rigged, tested, Unreal-checked. Free and local; nothing leaves the PC.

Generating the concept in a symmetrical T-pose (spread wings, all legs visible) is what makes the later rig easy, so the
pose is part of the prompt, and a vision model rejects pictures that did not follow it."""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from core import craftjobs, storage
from tools import charjobs, rigging
from tools.registry import tool

SEEDS = (11, 23, 37)
GOOD_SCORE = 6

_WINGED = re.compile(r"\b(?:dragon|wyvern|bird|angel|bat|griffin|gryphon|harpy|phoenix|winged|wings?)\b", re.I)
_FOUR = re.compile(r"\b(?:quadruped|four[- ]legged|dog|wolf|horse|cat|lion|bear|beast|deer|dinosaur|raptor)\b", re.I)
_MULTI = re.compile(r"\b(?:spider|insect|scorpion|octopus|tentacle|serpent|snake|hydra|centipede)\b", re.I)


def pose_prompt(description):
    d = description.strip().rstrip(".")
    base = ("full body character concept for a 3D model, single character, entire body visible from head to feet, "
            "plain flat light-grey background, even soft lighting, no cropping, nothing held or touching the body")
    if _FOUR.search(d) or _MULTI.search(d):
        pose = "standing in a neutral symmetrical stance seen exactly from the side, every leg clearly visible and separated"
    elif _WINGED.search(d):
        pose = ("standing symmetrically facing the camera with wings fully spread wide open and flat to both sides, "
                "arms and legs slightly apart")
    else:
        pose = "standing in a symmetrical T-pose facing the camera, arms straight out to the sides, legs slightly apart"
    return f"{d}, {pose}, {base}"


JUDGE_SCHEMA = {"type": "object", "properties": {
    "full_body_visible": {"type": "boolean"}, "single_character": {"type": "boolean"},
    "pose": {"type": "string", "enum": ["t_pose", "a_pose", "spread_wings", "side_view_standing", "other"]},
    "limbs_separated": {"type": "boolean"}, "plain_background": {"type": "boolean"},
    "score": {"type": "integer"}}, "required": ["full_body_visible", "single_character", "pose", "limbs_separated", "plain_background", "score"]}


def judge(img_path, vlm=None):
    """Local vision verdict on a concept image: usable as the base for rigging? score 0-10."""
    import base64
    if vlm is None:
        from core import engeyes
        vlm = engeyes._vlm
    b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
    ask = ("You check a character concept image that will be turned into a rigged 3D model. Answer from what you SEE. "
           "Is the whole body visible head to feet? Only one character? What pose (T-pose with arms straight out, A-pose, "
           "spread wings, side view standing on all legs, or other)? Are arms and legs separated from the torso and from "
           "each other? Is the background plain? Score 0-10 for how easy it is to rig: 10 = full body, symmetrical, limbs "
           "clearly separated, plain background.")
    v = vlm([b64], ask, JUDGE_SCHEMA)
    v["usable"] = bool(v.get("full_body_visible") and v.get("single_character") and v.get("score", 0) >= GOOD_SCORE)
    return v


def concepts(prompt, out_dir, gen=None, vlm=None, seeds=SEEDS):
    """Generate one candidate per seed, judge each, return (best_path, verdicts). gen/vlm injectable for tests."""
    if gen is None:
        from imaging import engine
        gen = lambda p, seed: engine.generate(p, seed=seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in seeds:
        craftjobs.step(f"drawing concept image {len(rows) + 1} of {len(seeds)}")
        img = gen(prompt, seed)
        path = out_dir / f"concept_seed{seed}.png"
        img.save(path)
        rows.append({"seed": seed, "path": str(path)})
    try:
        from imaging import engine
        engine.unload()                       # free the card before the vision model and the 3D generator need it
    except Exception:
        pass
    for r in rows:
        craftjobs.step("checking the pose with my vision model")
        try:
            r["verdict"] = judge(r["path"], vlm)
        except Exception as e:                # no vision model: keep going, say so, take the first
            r["verdict"] = {"usable": True, "score": 0, "note": f"not checked ({type(e).__name__})"}
    best = max(rows, key=lambda r: (r["verdict"].get("usable", False), r["verdict"].get("score", 0)))
    return best, rows


def _make(description, kind, profile):
    ws = Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace")) / "generated"
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower())[:30].strip("-") or "character"
    out = ws / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}"
    prompt = pose_prompt(description)
    best, rows = concepts(prompt, out)
    v = best["verdict"]
    (out / "concept.json").write_text(json.dumps({
        "description": description, "prompt": prompt, "chosen": best["path"], "candidates": rows,
        "generator": "FLUX.2-klein-4B (local); repo notes say Apache-2.0 for the 4B model, no licence file in the local cache",
    }, indent=2), encoding="utf-8")
    note = ""
    if not v.get("usable"):
        note = ("None of my three drawings was a clean full-body pose, so the rig may need a second try with a better picture. ")
    elif v.get("note"):
        note = "I could not check the pose with my vision model. "
    craftjobs.step("freeing the graphics card for the 3D generator")
    g = craftjobs.gpu_state(need_mb=7000)                    # the vision model just used the card: unload it before Hunyuan needs it
    if not g["ok"]:
        return f"I drew it as {v.get('pose', 'a pose')} ({best['path']}) but {g['say']}"
    craftjobs.step("turning the picture into a 3D model")
    result = rigging.image_to_character(best["path"], kind, profile)
    return f"I drew it as {v.get('pose', 'a pose')} (rig-friendliness {v.get('score', '?')}/10). {note}{result}"


@tool("Make a brand-new rigged 3D character or creature from a DESCRIPTION: draws a concept in a rig-friendly T-pose "
      "(or spread wings / side view for animals), checks it with the vision model, builds the 3D shape and rigs it, all "
      "locally and free, in the background. Use for 'make me a 3D dragon', 'create a character that is a ...'.",
      {"description": {"type": "string"}, "kind": {"type": "string", "enum": ["", "humanoid", "quadruped", "creature"]},
       "profile": {"type": "string", "enum": ["blender", "ue5"]}}, required=["description"], slow=True, direct=True)
def make_character_from_description(description: str, kind: str = "", profile: str = "ue5") -> str:
    d = (description or "").strip()
    if len(d) < 6:
        return "Tell me a bit more: what should the character look like?"
    if not kind and _FOUR.search(d):
        kind = "quadruped"
    return charjobs._start(f"making a 3D character: {d[:60]}", lambda: _make(d, kind, profile), need_gpu_mb=12000)
