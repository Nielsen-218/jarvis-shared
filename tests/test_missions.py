"""Mark VI part 1: the durable mission queue (core/missions.py). Owner's decisions 2026-11-10: queue on the laptop, an approval
waits until he answers, background only."""
import pytest

from core import missions, users


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MISSIONS_DIR", str(tmp_path / "missions"))
    users.set_current("owner")


def test_a_mission_is_queued_in_his_words_with_a_zero_cloud_budget():
    m = missions.add("  Update my game launchers   and tell me what changed ")
    assert m["goal"] == "Update my game launchers and tell me what changed" and m["state"] == "queued"
    assert m["budget"]["cloud_usd"] == 0.0 and m["budget"]["minutes"] > 0
    assert [x["id"] for x in missions.listing(active_only=True)] == [m["id"]]
    with pytest.raises(ValueError):
        missions.add("   ")
    with pytest.raises(ValueError):
        missions.add("x" * (missions.MAX_GOAL_CHARS + 1))


def test_the_worker_claims_the_oldest_one_atomically_and_reports_it():
    a, b = missions.add("first"), missions.add("second")
    got = missions.claim_next("pc")
    assert got["id"] == a["id"] and got["state"] == "running" and got["run"]
    assert missions.claim_next("pc")["id"] == b["id"]
    assert missions.claim_next("pc") is None                       # nothing left: never the same one twice
    done = missions.finish(got["run"], "done", "Launchers updated: Steam, Epic.")
    assert done["state"] == "done" and done["result"].startswith("Launchers")
    with pytest.raises(ValueError):
        missions.finish(got["run"], "cancelled")                   # only the owner cancels


def test_the_pause_switch_stops_new_work():
    missions.add("clean temp files")
    missions.set_paused(True)
    assert missions.paused() and missions.claim_next("pc") is None
    missions.set_paused(False)
    assert missions.claim_next("pc") is not None


def test_an_interrupted_mission_is_never_replayed_blindly():
    m = missions.add("rig the new character")
    missions.claim_next("pc")                                       # ... and the PC crashes mid-run
    assert missions.recover(stale_s=0) == [m["id"]]          # silent long enough (C1: recovery goes by silence)
    again = missions.get(m["id"])
    assert again["state"] == "queued" and again["retries"] == 1 and "check what was already done" in again["note"]
    missions.claim_next("pc")                                       # crashes a second time
    missions.recover(stale_s=0)
    final = missions.get(m["id"])
    assert final["state"] == "unknown" and "ask the owner" in final["note"]
    assert [r["outcome"] for r in missions.runs(m["id"])] == ["interrupted", "interrupted"]


def test_a_risky_step_waits_until_he_answers_and_no_is_no():
    m = missions.add("update the Nvidia driver")
    run = missions.claim_next("pc")["run"]
    parked = missions.park_for_approval(run, {"tool": "install", "arguments": {"name": "nvidia"}}, "Install driver 621.14?")
    assert parked["state"] == "waiting_approval" and parked["approval"]["preview"] == "Install driver 621.14?"
    assert missions.claim_next("pc") is None                        # parked: not picked up again until he answers
    m2 = missions.add("report disk space")
    assert missions.claim_next("pc")["id"] == m2["id"]              # a parked mission never holds up other work
    denied = missions.answer_approval(m["id"], False, missions.get(m["id"])["approval"]["fp"])
    assert denied["state"] == "blocked" and denied["approval"]["answer"] == "denied"
    with pytest.raises(ValueError):
        missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])                     # only a waiting mission takes an answer


def test_an_approval_continues_the_mission_from_the_parked_step():
    m = missions.add("update the Nvidia driver")
    run = missions.claim_next("pc")["run"]
    missions.park_for_approval(run, {"tool": "install"}, "Install?")
    ok = missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])
    assert ok["state"] == "queued" and ok["approval"]["answer"] == "approved" and "continues" in ok["note"]


def test_only_the_owner_cancels_and_a_finished_mission_stays_finished():
    m = missions.add("scan for big files")
    assert missions.cancel(m["id"])["state"] == "cancelled"
    with pytest.raises(ValueError):
        missions.cancel(m["id"])
    other = users.add("Maya", {"en": "ma'am"})                      # an unknown id falls back to the owner
    with users.acting_as(other):                                        # another person's missions are not hers
        assert missions.get(m["id"]) is None and missions.listing() == []
        with pytest.raises(ValueError):
            missions.cancel(m["id"])


def test_said_aloud_plainly():
    m = missions.add("Update my game launchers")
    assert missions.spoken(m) == '"Update my game launchers": queued'


def test_mission_requests_go_to_his_mission_skill_not_pc_steps():
    from core import gateway
    for said in ("Jarvis, clean up my downloads folder while I'm away.", "When I'm gone, run the security checkup.",
                 "What are my missions?", "Pause my missions."):
        d = gateway.exact(said)
        assert d and d.intents[0].kind == "chat", said


def test_the_voice_commands_queue_list_cancel_and_pause_locally(monkeypatch):
    from tools import missions as voice
    monkeypatch.setattr(voice, "_remote", lambda: False)                 # on the laptop: the store itself
    assert voice.start_mission("clean up my downloads folder").startswith("Mission queued, sir")
    assert '"clean up my downloads folder": queued' in voice.mission_status()
    assert voice.pause_missions(True).startswith("Missions paused") and "paused" in voice.mission_status()
    assert voice.cancel_mission("downloads").startswith("Cancelled the mission")
    assert voice.cancel_mission("") == "There's no active mission to cancel."


def test_on_the_pc_the_voice_commands_talk_to_the_laptop(monkeypatch):
    from tools import missions as voice
    calls = []

    def fake(method, path, body=None, query=None):
        calls.append((method, path, body, query))
        return {"mission": {"id": "abc", "goal": "x", "state": "queued"}, "missions": [], "paused": False}
    monkeypatch.setattr(voice, "_remote", lambda: True)
    monkeypatch.setattr(voice, "_call", fake)
    voice.start_mission("x")
    voice.mission_status()
    assert calls[0][:2] == ("POST", "/api/missions") and calls[1][:2] == ("GET", "/api/missions")


def test_plain_mission_sentences_run_the_real_skill(monkeypatch):
    """Live 2026-11-10: the brain answered 'Report my disk space while I'm away' with made-up numbers and 'What are my
    missions?' with 'Mission cancelled, sir.' - no skill ran."""
    from core import quick
    from tools import registry
    calls = []
    monkeypatch.setattr(registry, "run", lambda n, a: calls.append((n, a)) or ("ok", False))
    expect = {"Jarvis, report my disk space while I'm away.": ("start_mission", {"goal": "report my disk space"}),
              "While I'm gone, clean up my downloads folder.": ("start_mission", {"goal": "clean up my downloads folder"}),
              "What are my missions?": ("mission_status", {}),
              "Cancel that mission.": ("cancel_mission", {"which": ""}),
              "cancel the driver update mission": ("cancel_mission", {"which": "driver update"}),
              "Pause my missions.": ("pause_missions", {"paused": True}),
              "resume the missions": ("pause_missions", {"paused": False})}
    for said, call in expect.items():
        calls.clear()
        quick.handle(said)
        assert calls == [call], said
    calls.clear()
    assert quick.handle("I'm away this weekend") is None and calls == []


# ---- Mark VI C1 (2026-11-10): checkpoint, heartbeat, release, consume, recovery by silence -------------------------------
def test_a_parked_step_resumes_from_its_checkpoint_and_the_approval_is_used_once():
    from core.pcledger import fingerprint
    m = missions.add("security checkup, clean temp files, report disk space")
    run = missions.claim_next("pc")["run"]
    step = {"tool": "pc_cleanup", "arguments": {"what": "temp"}}
    parked = missions.park_for_approval(run, step, "Delete 1.2 GB of temp files?", {"done_clauses": [0], "evidence": ["scan ok"]})
    assert parked["changed"] and parked["approval"]["fp"] == fingerprint(step) and parked["approval"]["used"] is None
    assert missions.consume_approval(m["id"]) is None                 # not answered yet: nothing to use
    missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])
    again = missions.claim_next("pc")
    assert again["id"] == m["id"] and again["checkpoint"] == {"done_clauses": [0], "evidence": ["scan ok"]}
    assert again["approval"]["answer"] == "approved"
    used = missions.consume_approval(m["id"])
    assert used and used["step"] == step and used["used"]
    assert missions.consume_approval(m["id"]) is None                 # never twice


def test_a_second_finish_changes_nothing():
    missions.add("report disk space")
    run = missions.claim_next("pc")["run"]
    assert missions.finish(run, "done", "C: 120 GB free")["changed"] is True
    second = missions.finish(run, "failed", "late")
    assert second["changed"] is False and second["state"] == "done" and second["result"] == "C: 120 GB free"


def test_recovery_leaves_a_fresh_heartbeat_alone_and_requeues_a_silent_run(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(missions.time, "time", lambda: clock[0])
    m = missions.add("scan for big files")
    run = missions.claim_next("pc")["run"]
    clock[0] += 500
    assert missions.heartbeat(run, "step 2 of 3") == {"state": "running", "paused": False}
    clock[0] += 500                                                   # 1000 s since the start, 500 s since the heartbeat
    assert missions.recover(stale_s=600) == []
    assert missions.get(m["id"])["state"] == "running"
    clock[0] += 200
    assert missions.recover(stale_s=600) == [m["id"]]
    assert missions.get(m["id"])["state"] == "queued"
    assert missions.heartbeat(run, "late")["state"] == "ended"        # the old worker learns to stop
    assert missions.finish(run, "done", "late")["changed"] is False


def test_a_heartbeat_tells_the_worker_about_cancel_and_pause():
    m = missions.add("scan for big files")
    run = missions.claim_next("pc")["run"]
    missions.set_paused(True)
    assert missions.heartbeat(run, "x")["paused"] is True
    missions.cancel(m["id"])
    assert missions.heartbeat(run, "x")["state"] == "cancelled"


def test_release_requeues_with_the_checkpoint_and_is_not_a_retry():
    m = missions.add("rig the new character")
    run = missions.claim_next("pc")["run"]
    out = missions.release(run, {"done_clauses": [0]})
    assert out["changed"] and out["state"] == "queued" and out["retries"] == 0 and out["checkpoint"] == {"done_clauses": [0]}
    assert [r["outcome"] for r in missions.runs(m["id"])] == ["yielded"]
    assert missions.claim_next("pc")["checkpoint"] == {"done_clauses": [0]}


def test_the_worker_claims_only_that_persons_missions():
    missions.add("the owner's mission")
    other = users.add("Maya", {"en": "ma'am"})
    with users.acting_as(other):
        assert missions.claim_next("pc") is None
        with pytest.raises(ValueError):
            missions.finish("nope", "done")


def test_the_queue_is_copied_once_a_day_and_the_last_seven_kept(tmp_path, monkeypatch):
    """Cross-device audit 9: the queue lived on one machine's disk only."""
    import sqlite3
    monkeypatch.setenv("JARVIS_MISSIONS_BACKUP_DIR", str(tmp_path / "copies"))
    assert missions.backup_daily(today="20261101") is None, "no database yet: nothing to copy"
    m = missions.add("tidy the downloads folder")
    first = missions.backup_daily(today="20261101")
    assert first and first.name == "missions-20261101.sqlite"
    assert missions.backup_daily(today="20261101") is None, "one copy per day"
    db = sqlite3.connect(first)
    try:
        assert db.execute("SELECT id FROM missions").fetchall() == [(m["id"],)], "the copy holds the queue"
    finally:
        db.close()
    for day in range(2, 11):
        missions.backup_daily(today=f"202611{day:02d}")
    kept = sorted(p.name for p in (tmp_path / "copies").glob("missions-*.sqlite"))
    assert len(kept) == missions.BACKUPS_KEPT and kept[-1] == "missions-20261110.sqlite" and not list((tmp_path / "copies").glob("*.partial"))


def test_the_default_backup_folder_is_under_the_synced_data_folder():
    assert missions.backup_dir().parts[-2:] == ("backups", "missions")
