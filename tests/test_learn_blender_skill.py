"""The stored two-bone procedure in REAL headless Blender, judged by the unmodified independent judge. Skipped when Blender is absent."""
import ast
import re
from pathlib import Path

import pytest

from core import learn_blender_basics as bb
from core import learn_blender_skill as sk
from core import procedures
from core.dojo_store import Store

try:
    from tools import rigging
    HAVE = Path(str(rigging.blender_exe())).exists()
except Exception:
    HAVE = False
needs_blender = pytest.mark.skipif(not HAVE, reason="Blender not installed")


def test_procedure_library_knows_no_skill():
    tree = ast.parse(Path(procedures.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not names & {"bpy", "blender", "rigging", "dojo", "learn_blender_basics"}


def test_skill_subclass_shares_the_judge_hash_with_the_basics_adapter(tmp_path):
    from core.learn import Engine
    a, b = Engine(bb.BlenderBasicsAdapter(), Store("blender_basics")), Engine(sk.BlenderSkillAdapter(), Store("blender_basics"))
    assert a.judge_hash() == b.judge_hash()


@needs_blender
def test_registered_skill_passes_judge_recovers_and_declines(tmp_path):

    lib = procedures.Library(tmp_path / "p")
    ev = {"judge": "test", "passed": 24, "total": 24, "tasks": []}
    lib.register(sk.PID, "t", sk.SOURCE.read_text(encoding="utf-8"), sk.TRIGGERS, sk.PARAMS, sk.RECOVERY, "test", ev)
    task = bb.tasks("train")[8]                                            # an X tube
    m = lib.match(bb.TASK_TEXT, task["variant"])
    assert m
    assert bb.judge(task["variant"], lib.render(sk.PID, m["params"]))["full"]
    thick = {"axis": "z", "start": 0.0, "end": 1.0, "radius": 0.2}         # out of the verified range: refused with a named reason, not a bad bind
    m2 = lib.match(bb.TASK_TEXT, thick)
    r = bb.judge(thick, lib.render(sk.PID, m2["params"]))
    man, _ = lib.get(sk.PID)
    assert not r["full"] and re.search("too_thick", r["error"]) and lib.recovery_for(man, r["error"])["ask_user"]
    stale = "import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Rig', None))\n"
    first = bb.judge(task["variant"], stale + lib.render(sk.PID, m["params"]))
    rec = lib.recovery_for(man, first["error"])
    assert rec["class"] == "rig_exists"
    assert bb.judge(task["variant"], stale + lib.render(sk.PID, {**m["params"], **rec["set"]}))["full"]
