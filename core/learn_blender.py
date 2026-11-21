"""Blender adapter for the learning core (core/learn.py): a thin wrapper around the existing dojo and the production rigging engine.
Nothing here re-implements rigging; the judge is the protected one (pose/motion/skin/export/round-trip tests, optional Unreal import).

Tasks are the corpus creatures (development vs held-out). Actions are named engine strategies (parameter sets), the same knobs the
dojo already tunes; a learned code overlay is NOT an action here (those stay behind the owner-review gate)."""
import math
import time
from pathlib import Path

from core import dojo, learn
from core.config import ROOT

STRATEGIES = {
    "default": {},
    "center5": {"anat_center_iters": 5},
    "center6": {"anat_center_iters": 6},
    "simplify_fine": {"anat_simplify": 0.4},
    "wide_sheets": {"anat_sheet_cut": 4},
}


class BlenderAdapter(learn.Adapter):
    skill = "blender"

    def __init__(self, cases=None, strategies=None):
        self._cases = cases
        self.strategies = strategies or STRATEGIES

    def _all(self):
        rows = self._cases if self._cases is not None else dojo.corpus()
        return [c for c in rows if c.get("kind") == "creature" and Path(c["resolved"]).is_file()]

    # --- contract
    def _burned(self):
        """Cases the dojo already studied (their failures were read): no longer clean held-out material."""
        try:
            from core.dojo_store import Store
            return {k for k, v in Store("blender").cases().items() if v.get("burned")}
        except Exception:
            return set()

    def tasks(self, split):
        burned = self._burned()
        out = []
        for c in self._all():
            clean_heldout = c.get("split") == "heldout" and c["name"] not in burned
            if (split == "holdout") != clean_heldout:             # train = development cases + held-out cases the dojo already studied
                continue
            p = Path(c["resolved"])
            out.append({"id": "bl:" + c["name"], "label": c["name"], "case": c, "digest": learn.file_hash(p), "bytes": p.stat().st_size})
        return out

    def actions(self):
        return list(self.strategies)

    def default_action(self):
        return "default"

    def action_kind(self, action):
        return "param"

    def judge_files(self):
        hub = ROOT / "hub"
        return [hub / "creature_acceptance.py", hub / "anatomy.py", hub / "rigging.py", ROOT / "core" / "dojo.py"]

    def features(self, task):
        return {"bias": 1.0, "log_bytes": min(math.log10(max(task["bytes"], 1)) / 8, 1.0),
                "generated": float(task["case"].get("type", "").startswith("generated")),
                "synthetic": float(task["case"].get("type", "").startswith("synthetic"))}

    def execute(self, task, action, ctx):
        t0 = time.monotonic()
        run = dojo.run_case(ctx["store"], task["case"], params=self.strategies[action] or None, tag="learn")
        if "score" not in run:
            return {"full": False, "stages_ok": 0, "fail_count": 1, "total": 1, "error": run.get("error"), "cost_s": round(time.monotonic() - t0, 1)}
        s = run["score"]
        return {"full": s["full"], "stages_ok": s["stages_ok"], "fail_count": s["fail_count"], "total": s["total"],
                "cost_s": run["seconds"], "hints": [], "detail": {"pose": s["pose"], "failing": s["failing"][:8]}}
