"""Visual evaluation of a rig by the local vision model, as a SECOND OPINION next to the measured tests.

The multi-view renders (front/side/top, skeleton drawn as real geometry) are shown to qwen3-vl, which says what creature it
sees, how many legs/wings/heads, whether the skeleton covers the body, and what looks wrong. Vision is uncertain, so its
verdict is recorded with a confidence and a comparison to the geometric plan; it never certifies success on its own."""
import base64
import json
from pathlib import Path

from core import dojo

VIEW_SCHEMA = {"type": "object", "properties": {
    "creature_guess": {"type": "string"},
    "counts": {"type": "object", "properties": {"legs": {"type": "integer"}, "wings": {"type": "integer"}, "heads": {"type": "integer"},
                                                  "tails": {"type": "integer"}, "arms": {"type": "integer"}}},
    "skeleton_covers_body": {"type": "boolean"},
    "bones_outside_body": {"type": "boolean"},
    "problems": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "number"}},
    "required": ["creature_guess", "counts", "skeleton_covers_body", "confidence"]}

ASK = ("These are three orthographic views (front, side, top) of a 3D creature (grey) with its generated skeleton drawn as red "
       "bone shapes inside it. Answer ONLY from what you can see. Which creature or body plan is it? How many legs, wings, heads, "
       "tails and arms does the BODY have (count the body, not the bones)? Does the skeleton reach every limb, wing and the tail, or "
       "are parts of the body without bones? Are there bones floating outside the body? List concrete problems you can see. "
       "Give your confidence from 0 to 1; if the views are unclear, say so with a low confidence.")


def review(folder, plan=None, ask=None):
    """-> verdict dict (also saved next to the report), or None when no images / no model."""
    folder = Path(folder)
    imgs = [folder / f"view_{v}.png" for v in ("front", "side", "top")]
    if not all(p.is_file() for p in imgs):
        return None
    ask = ask or dojo.ask_json
    b64 = [base64.b64encode(p.read_bytes()).decode() for p in imgs]
    try:
        v = ask(ASK, VIEW_SCHEMA, images=b64, temperature=0.1)
    except Exception as e:
        return {"error": f"vision unavailable: {type(e).__name__}"}
    v["confidence"] = max(0.0, min(1.0, float(v.get("confidence", 0.0) or 0.0)))
    if plan:
        c = plan.get("counts", {})
        mine = {"legs": c.get("leg", 0), "wings": plan.get("wing_parts", 0), "heads": c.get("head_end", 0), "arms": c.get("arm", 0)}
        theirs = v.get("counts") or {}
        v["agreement"] = {k: {"geometry": mine[k], "vision": theirs.get(k)} for k in mine if theirs.get(k) is not None}
        v["disagrees_on"] = [k for k, d in v["agreement"].items() if d["geometry"] != d["vision"]]
    v["note"] = "second opinion from a local vision model: uncertain, never sufficient on its own"
    (folder / "visual_review.json").write_text(json.dumps(v, indent=1), encoding="utf-8")
    return v
