"""The Blender dojo as the FIRST adapter of the learning core: same verdict as the dojo, no rewrite of the rigging engine."""
import pytest

from core import dojo, learn
from core.config import ROOT
from core.dojo_store import Store
from core.learn_blender import BlenderAdapter
from tools import rigging

needs_blender = pytest.mark.skipif(rigging.blender_exe() is None, reason="Blender not installed")
from synth_fixtures import synthetic_meshes  # noqa: F401  (pytest fixture: generates the gitignored synthetic meshes)


@pytest.mark.usefixtures("synthetic_meshes")
def test_blender_adapter_fills_the_contract():
    a = BlenderAdapter()
    assert a.validate()
    assert {t["label"] for t in a.tasks("train")}.isdisjoint({t["label"] for t in a.tasks("holdout")})
    assert "default" in a.actions() and a.action_kind("center5") == "param"
    assert dojo.better({"full": False, "stages_ok": 2, "fail_count": 3, "total": 20}, {"full": False, "stages_ok": 2, "fail_count": 5, "total": 20}) is True


@needs_blender
@pytest.mark.usefixtures("synthetic_meshes")
def test_adapter_matches_the_dojo_verdict(tmp_path):
    a = BlenderAdapter()
    store = Store("blender", root=tmp_path / "s")
    e = learn.Engine(a, store=store, gaming=lambda: False)
    e.seal_holdout()
    task = next(t for t in a.tasks("holdout") if t["label"] == "heldout_starfish")
    row = e.run(task["id"], "default", purpose="exam")
    direct = dojo.run_case(store, task["case"], tag="direct")["score"]
    assert (row["outcome"]["full"], row["outcome"]["fail_count"], row["outcome"]["total"]) == (direct["full"], direct["fail_count"], direct["total"])


def test_core_has_no_blender_imports():
    import ast
    tree = ast.parse((ROOT / "core" / "learn.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if "dojo" in m and m != "core.dojo_store" or "rigging" in m or m == "bpy"}
