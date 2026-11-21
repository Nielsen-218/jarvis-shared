"""Verified Blender procedures as a Jarvis skill: the owner's words -> the stored procedure (core/procedures.py) -> real Blender -> the independent judge.

One tool per verified procedure family. Nothing here knows how to bind a mesh: the stored, hash-checked procedure does, and the judge
(hub/learn_basics_judge.py, unmodified) is the only authority on whether it worked. Success is announced ONLY when the judge passes."""
import hashlib

from core import learn_blender_basics as bb
from core import learn_blender_skill as sk
from core.dojo_store import Store
from tools.registry import tool


def _store():
    return Store("blender_basics")


def wants(text):
    """Should the chat front door hand this request to bind_two_bone_limb? Cheap pre-filter, then the stored procedure's own trigger/exclusion words."""
    if not sk.gate(text):
        return False
    try:
        lib = sk.library(_store())
        got = lib.get(sk.PID) if sk.PID in lib.ids() else None
        return bool(got and lib.triggered(got[0], text))
    except Exception:                       # a broken store must never swallow the turn: the normal conversation takes it
        return False


@tool("Bind a straight tube to a two-bone armature with automatic weights and PROVE it deforms: builds the tube in headless Blender, runs Jarvis's "
      "stored verified procedure, then bends the lower bone 70 degrees and measures the mesh with an independent judge. Use for 'bind a tube to a rig with "
      "exactly two bones (upper and lower) using automatic weights'. NOT for characters, creatures, dragons, more than two bones, bent meshes or the "
      "mesh in the open Blender scene. Free, local, no Claude call. Pass the owner's words as `request`.",
      {"request": {"type": "string"}}, slow=True, direct=True)
def bind_two_bone_limb(request: str) -> str:
    u = sk.understand(request)
    if u["decline"]:
        return u["decline"]
    store = _store()
    lib = sk.library(store)
    got = lib.get(sk.PID) if sk.PID in lib.ids() else None
    if not got or not lib.triggered(got[0], request):
        return "I don't have a verified procedure that covers that request, so I won't improvise one."
    v = u["variant"]
    task = {"id": "live:" + hashlib.sha1(repr(sorted(v.items())).encode()).hexdigest()[:8], "variant": v}
    verdict, trail = sk.BlenderSkillAdapter().run_procedure(task, store, text=u["canonical"])
    shape = f"a {v['end'] - v['start']:g}-long tube along {v['axis']}, radius {v['radius']:g}"
    note = (" I assumed " + "; ".join(u["assumed"]) + ".") if u["assumed"] else ""
    if verdict is None:
        return "I don't have a verified procedure that covers that request, so I won't improvise one."
    if verdict["full"]:
        again = " after one automatic recovery" if len(trail) > 1 else ""
        return (f"Done and verified. I bound {shape} to a two-bone rig (upper and lower){again}. In real Blender I bent the lower bone 70 degrees: "
                f"the lower half followed, the upper half stayed put, nothing tore, and all {verdict['total']} independent checks passed. "
                f"This used my stored procedure version {got[0]['version']}, with no Claude call.{note}")
    err = (verdict.get("error") or "")
    if "too_thick" in err:
        return (f"I won't bind that one: {shape} is thicker than the range my procedure was verified on (radius up to 0.135 of the length), "
                f"and it tears when bent. Try a thinner tube or a longer one.")
    return (f"I ran my stored procedure on {shape}, but the independent check did NOT pass ({verdict['fail_count']} of {verdict['total']} checks failed: "
            f"{', '.join(verdict['failing'][:3])}). I'm not calling that bound.{note}")
