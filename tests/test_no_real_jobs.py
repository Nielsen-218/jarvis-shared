"""Negative routing tests must never run real background work (incident 2026-11-01, docs/learning/INCIDENTS.md)."""
import time

import pytest

from core import craftintent, craftjobs
from tools import charjobs, rigging

NEGATIVE_PHRASES = ["rig my dragon with exactly two bones and bind it with automatic weights",
                    "bind a tube to an armature with three bones using automatic weights",
                    "how do I bind a tube to a rig with two bones using automatic weights",
                    "rig my character", "practice rigging for an hour"]


def test_routing_is_decided_without_executing_anything(monkeypatch):
    monkeypatch.setattr(charjobs, "_start", lambda *a, **k: pytest.fail("routing must not start a job"))
    for phrase in NEGATIVE_PHRASES:
        hit = craftintent.match(phrase)                      # a pure function: it names the tool, it never runs it
        assert hit is None or hit[0] != "bind_two_bone_limb"


def test_the_real_heavy_entry_points_are_blocked_in_tests():
    for call in (lambda: rigging.rig_character("x.glb"), lambda: rigging.image_to_character("x.png")):
        with pytest.raises(AssertionError, match="real background work"):
            call()


def test_a_rig_request_that_finds_a_real_file_cannot_start_real_rigging(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_JOBS_INLINE", raising=False)          # the live (background) mode, as in the incident
    monkeypatch.setattr(craftjobs, "_jobs", {})
    glb = tmp_path / "dragon.glb"
    glb.write_bytes(b"glTF")
    charjobs.rig_my_character(str(glb))
    for _ in range(100):
        j = craftjobs.get()
        if j and j["state"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j["state"] == "failed" and "real background work" in (j.get("result") or j.get("error") or str(j))
    assert not list(tmp_path.glob("*rigged*"))


@pytest.mark.real_jobs
def test_the_marker_lifts_the_guard():
    assert rigging.rig_character.__name__ == "rig_character"


def test_a_practice_session_with_the_real_runner_cannot_start_in_a_test(tmp_path):
    from core import dojo
    from core.dojo_store import Store
    with pytest.raises(AssertionError, match="real background work"):
        dojo.Practice(Store(root=tmp_path / "d")).start()
