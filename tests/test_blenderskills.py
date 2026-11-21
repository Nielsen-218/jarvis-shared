"""The live path: the owner's words -> chat front door -> stored verified procedure -> real headless Blender -> independent judge -> spoken answer.
The Blender tests are skipped when Blender is absent. A fresh temp store holds the procedure, as after a clean registration."""
from pathlib import Path

import pytest

from core import craftintent
from core import learn_blender_basics as bb
from core import learn_blender_skill as sk
from core.dojo_store import Store
from tools import blenderskills
from tools.registry import run

try:
    from tools import rigging
    HAVE = Path(str(rigging.blender_exe())).exists()
except Exception:
    HAVE = False
needs_blender = pytest.mark.skipif(not HAVE, reason="Blender not installed")

ASK = "Bind a tube mesh named Limb to an armature named Rig with exactly two bones upper and lower using automatic weights, axis z from 0 to 1."


@pytest.fixture()
def stored(tmp_path, monkeypatch):
    store = Store("blender_basics", root=tmp_path / "bb")
    sk.register_from_evidence(store, {"judge": "test", "passed": 24, "total": 24, "tasks": []})
    monkeypatch.setattr(blenderskills, "_store", lambda: store)
    return store


def test_natural_request_reaches_the_verified_tool_and_nothing_else_does(stored):
    name, args = craftintent.match(ASK)
    assert name == "bind_two_bone_limb" and args["request"].startswith("Bind a tube")
    assert craftintent.match("Please skin a 1.5 meters long tube along the x axis to a rig with exactly two bones and automatic weights")[0] == "bind_two_bone_limb"
    for text in ("rig my dragon with exactly two bones and bind it with automatic weights",             # a creature: the general rigger, not this skill
                 "bind a tube to an armature with three bones using automatic weights",                  # not two bones
                 "bind the spine and tail of my character to a rig with two bones using automatic weights",
                 "how do I bind a tube to a rig with two bones using automatic weights",                  # a question, not a job
                 "rig my character"):
        hit = craftintent.match(text)
        assert hit is None or hit[0] != "bind_two_bone_limb", text


def test_without_a_stored_procedure_it_is_never_claimed(tmp_path, monkeypatch):
    monkeypatch.setattr(blenderskills, "_store", lambda: Store("blender_basics", root=tmp_path / "empty"))
    assert not blenderskills.wants(ASK)
    assert (craftintent.match(ASK) or ("",))[0] != "bind_two_bone_limb"
    out, err = run("bind_two_bone_limb", {"request": ASK})
    assert "don't have a verified procedure" in out


def test_parameters_are_read_from_the_words_and_defaults_are_disclosed():
    u = sk.understand("skin a 2 meters long tube along the x axis to a rig with two bones named arm and forearm, radius 0.1")
    assert u["variant"] == {"axis": "x", "start": 0.0, "end": 2.0, "radius": 0.1}
    assert any("independent check" in a for a in u["assumed"])
    u = sk.understand(ASK)
    assert u["variant"]["axis"] == "z" and (u["variant"]["start"], u["variant"]["end"]) == (0.0, 1.0)
    assert sk.understand("bind a bent tube to a rig with two bones")["decline"]
    assert sk.understand("bind my tube in my open scene to a rig with exactly two bones")["decline"]


def test_success_is_announced_only_when_the_judge_passes(stored, monkeypatch):
    bad = {"full": False, "stages_ok": 1, "fail_count": 2, "total": 9, "failing": ["no_torn_edges_when_bent", "upper_half_stays_put"], "error": "x"}
    monkeypatch.setattr(bb, "judge", lambda variant, script, **k: bad)
    out, err = run("bind_two_bone_limb", {"request": ASK})
    assert "did NOT pass" in out and "Done and verified" not in out


@needs_blender
def test_end_to_end_in_real_blender_with_recovery_and_declines(stored):
    out, err = run("bind_two_bone_limb", {"request": ASK})
    assert not err and out.startswith("Done and verified") and "all 9 independent checks passed" in out and "no Claude call" in out
    thick, _ = run("bind_two_bone_limb", {"request": "bind a tube along the z axis from 0 to 1 with radius 0.3 to a rig with exactly two bones using automatic weights"})
    assert "won't bind that one" in thick and "Done and verified" not in thick
    assert len(sk.library(stored).uses()) >= 2                                                                    # every use is logged
