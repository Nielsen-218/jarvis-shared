"""Mark VI C12: a mission's craft job is quiet (no Blender window, no push of its own), the worker waits for it and checks
the delivered file, and the scheduler runs the worker on the desk PC only. Fakes only: no Blender, no GPU, no push."""
from pathlib import Path

import pytest

from core import craftintent, craftjobs, missions, missionworker as mw, notify, scheduler
from tests.test_missionworker import CALM, world  # noqa: F401  (the fake laptop + fake model fixture)
from tools import charjobs, registry, vault


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A finished 'rig' folder, the delivery folder, and spies on the two things a quiet job must never do."""
    src = tmp_path / "workspace" / "hero-rig"
    src.mkdir(parents=True)
    (src / "char_rigged.glb").write_bytes(b"glTF")
    monkeypatch.setenv("JARVIS_RIGGED_DIR", str(tmp_path / "delivered"))
    spies = {"opened": [], "pushed": []}
    monkeypatch.setattr(charjobs, "_open_file", lambda p: spies["opened"].append(p) or (True, "opened"))
    monkeypatch.setattr(notify, "notify", lambda *a, **k: spies["pushed"].append(a))
    from tools import gaming
    monkeypatch.setattr(gaming, "game_mode", lambda: False)
    spies["result"] = f"Rigged and tested. Files are in {src}. "
    return spies


def test_a_quiet_job_never_opens_blender_or_pushes(rig):
    with craftjobs.quiet_jobs():
        j = craftjobs.submit("rigging hero", lambda: charjobs._deliver(rig["result"]), wait=True)
    assert j["quiet"] and j["state"] == "done"
    assert rig["opened"] == [] and rig["pushed"] == []
    assert "did not open Blender" in j["result"] and charjobs.last_delivery()["file"].endswith("char_rigged.glb")


def test_the_owners_own_job_still_opens_and_announces(rig):
    j = craftjobs.submit("rigging hero", lambda: charjobs._deliver(rig["result"]), wait=True)
    assert not j["quiet"] and len(rig["opened"]) == 1 and len(rig["pushed"]) == 1


def test_charjobs_start_passes_quiet_through(rig, monkeypatch):
    monkeypatch.setenv("JARVIS_JOBS_INLINE", "1")
    charjobs._start("rigging hero", lambda: charjobs._deliver(rig["result"]), quiet=True)
    assert craftjobs.get()["quiet"] and rig["opened"] == [] and rig["pushed"] == []


@pytest.mark.parametrize("inline", [True, False])
def test_a_mission_rig_is_quiet_waited_for_and_checked(world, rig, monkeypatch, inline):  # noqa: F811
    if inline:
        monkeypatch.setenv("JARVIS_JOBS_INLINE", "1")
    else:
        monkeypatch.delenv("JARVIS_JOBS_INLINE", raising=False)
    monkeypatch.setattr(mw, "CRAFT_POLL_S", 0.01)
    monkeypatch.setattr(craftintent, "match", lambda text: ("rig_my_character", {"which": "hero"}))
    monkeypatch.setitem(registry._TOOLS, "rig_my_character", {
        "fn": lambda which="": charjobs._start("rigging hero", lambda: charjobs._deliver(rig["result"])), "slow": False,
        "schema": {"name": "rig_my_character", "input_schema": {"type": "object", "properties": {}}}})
    m = missions.add("rig the hero character")
    assert mw.tick(gate=CALM) == "done"
    got = missions.get(m["id"])
    assert got["state"] == "done" and "did not open Blender" in got["result"]
    assert rig["opened"] == [] and rig["pushed"] == []                 # no window in front, no second push
    assert craftjobs.get()["quiet"] and craftjobs.get()["state"] == "done"


def test_a_failed_rig_job_is_not_done(world, rig, monkeypatch):  # noqa: F811
    monkeypatch.setenv("JARVIS_JOBS_INLINE", "1")
    monkeypatch.setattr(craftintent, "match", lambda text: ("rig_my_character", {}))

    def boom():
        raise RuntimeError("blender crashed")
    monkeypatch.setitem(registry._TOOLS, "rig_my_character", {
        "fn": lambda: charjobs._start("rigging hero", boom), "slow": False,
        "schema": {"name": "rig_my_character", "input_schema": {"type": "object", "properties": {}}}})
    m = missions.add("rig the hero character")
    assert mw.tick(gate=CALM) == "blocked"                              # the judge said done; the hard check did not
    assert missions.get(m["id"])["state"] == "blocked" and rig["opened"] == []


def test_rigging_is_allowed_unattended_only_now_that_it_is_quiet():
    from core import missionpolicy
    assert missionpolicy.unattended("rig_my_character", {}) and missionpolicy.unattended("image_to_character", {})


# ---- the scheduler wiring
def _sched():
    return scheduler.Scheduler.__new__(scheduler.Scheduler)


def test_the_scheduler_never_runs_missions_on_the_server(monkeypatch):
    spawned = []
    monkeypatch.setattr(scheduler.Scheduler, "_spawn", lambda self, name, target: spawned.append((name, target)) or True)
    monkeypatch.setattr(vault, "_remote", lambda: ("http://laptop", "t"))
    monkeypatch.setattr(scheduler, "ROLE", "server")
    assert _sched()._missions() is False and spawned == []


def test_the_scheduler_needs_the_laptop_queue(monkeypatch):
    spawned = []
    monkeypatch.setattr(scheduler.Scheduler, "_spawn", lambda self, name, target: spawned.append((name, target)) or True)
    monkeypatch.setattr(scheduler, "ROLE", "desk")
    monkeypatch.setattr(vault, "_remote", lambda: None)
    assert _sched()._missions() is False and spawned == []
    monkeypatch.setattr(vault, "_remote", lambda: ("http://laptop", "t"))
    assert _sched()._missions() is True and spawned == [("missions", mw.tick)]
    monkeypatch.setenv("JARVIS_MISSIONS", "off")
    assert _sched()._missions() is False


def test_the_loop_calls_the_mission_wiring():
    import inspect
    assert "self._missions()" in inspect.getsource(scheduler.Scheduler._loop)
    assert Path(scheduler.__file__).read_text(encoding="utf-8").count('self._spawn("missions"') == 1
