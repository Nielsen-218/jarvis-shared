"""Procedure library: registration needs judge evidence, tampering is refused, matching is narrow, params are validated, restart keeps everything."""
import json

import pytest

from core import learn_blender_basics as bb
from core import learn_blender_skill as sk
from core import procedures

GOOD = {"judge": "abc", "passed": 24, "total": 24, "tasks": ["t"]}


def lib_with(tmp_path, evidence=GOOD):
    lib = procedures.Library(tmp_path / "p")
    lib.register(sk.PID, "t", "def run(**k):\n    return k\n", sk.TRIGGERS, sk.PARAMS, sk.RECOVERY, "test", evidence, entry="run")
    return lib


def test_registration_requires_full_judge_evidence(tmp_path):
    lib = procedures.Library(tmp_path / "p")
    for bad in (None, {"judge": "x", "passed": 23, "total": 24}, {"judge": "x", "passed": 5, "total": 5}, {"passed": 24, "total": 24}):
        with pytest.raises(procedures.NotVerified):
            lib.register(sk.PID, "t", "x=1", sk.TRIGGERS, sk.PARAMS, sk.RECOVERY, "test", bad)
    assert lib.ids() == []


def test_survives_restart_and_refuses_tampering(tmp_path):
    lib_with(tmp_path)
    again = procedures.Library(tmp_path / "p")                 # a new object, as after a restart
    man, src = again.get(sk.PID)
    assert man["evidence"]["passed"] == 24 and "def run" in src
    (tmp_path / "p" / sk.PID / "v1" / "source.py").write_text("import os\n", encoding="utf-8")
    with pytest.raises(procedures.NotVerified):
        procedures.Library(tmp_path / "p").get(sk.PID)


def test_matches_the_task_and_fills_parameters_from_text_and_context(tmp_path):
    lib = lib_with(tmp_path)
    m = lib.match(bb.TASK_TEXT, context={"axis": "x", "start": 0.5, "end": 2.0})
    assert m and m["id"] == sk.PID
    assert m["params"]["mesh_name"] == "Limb" and m["params"]["rig_name"] == "Rig" and m["params"]["bone_names"] == ["upper", "lower"]
    assert (m["params"]["axis"], m["params"]["start"], m["params"]["end"]) == ("x", 0.5, 2.0)


def test_does_not_stretch_over_work_it_was_not_verified_for(tmp_path):
    lib = lib_with(tmp_path)
    ctx = {"axis": "z", "start": 0.0, "end": 1.0}
    assert lib.match(bb.TASK_TEXT.replace("exactly two bones", "exactly three bones"), ctx) is None
    assert lib.match("Rig this dragon character with a spine and a tail and skin it.", ctx) is None
    assert lib.match("Fix the off-by-one error in this Python function.", ctx) is None
    assert lib.match(bb.TASK_TEXT, {"axis": "w", "start": 0, "end": 1}) is None              # a bad axis is not guessed
    assert lib.match(bb.TASK_TEXT, {}) is None                                                # missing parameters are not invented


def test_render_uses_validated_literals_only(tmp_path):
    lib = lib_with(tmp_path)
    p = {"mesh_name": "Limb", "rig_name": "Rig", "bone_names": ["upper", "lower"], "axis": "y", "start": 0.0, "end": 1.0, "bands": [0.15], "if_exists": "error"}
    script = lib.render(sk.PID, p)
    assert script.rstrip().endswith(")") and "axis=\"y\"" in script
    with pytest.raises(procedures.BadParams):
        lib.render(sk.PID, {**p, "axis": "y'); import os; ('"})
    with pytest.raises(procedures.BadParams):
        lib.render(sk.PID, {**p, "extra": 1})
    with pytest.raises(procedures.BadParams):
        lib.render(sk.PID, {**p, "start": "0; import os"})


def test_recovery_table_maps_errors_to_actions(tmp_path):
    lib = lib_with(tmp_path)
    man, _ = lib.get(sk.PID)
    assert lib.recovery_for(man, "SkillError: rig_exists: an object named Rig")["set"] == {"if_exists": "rename_old"}
    assert lib.recovery_for(man, "SkillError: mesh_missing: no mesh")["ask_user"] is True
    assert lib.recovery_for(man, "ZeroDivisionError") is None


def test_skill_source_passes_the_same_script_guard_as_any_generated_script():
    assert bb.guard(sk.SOURCE.read_text(encoding="utf-8") + "\nrun()\n") is None


def test_usage_log_is_persistent(tmp_path):
    lib = lib_with(tmp_path)
    lib.log(task="t1", matched=sk.PID)
    assert procedures.Library(tmp_path / "p").uses()[-1]["task"] == "t1"
