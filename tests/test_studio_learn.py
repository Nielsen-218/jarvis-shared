"""The studio's durable knowledge (core/studiolearn): verified lessons only from green orders, failures kept apart,
retrieval into the next order and the worker's prompt, the codebase map, the dataset log, and the learning metrics.
Includes the 2026-11-16 review-round-3 regressions."""
import json
import subprocess

import pytest

from core import missions, studio, studiojob, studiolearn
from tests.test_studio import _git, _tick, credit, lsw, no_real_credit, queue  # noqa: F401 - fixtures

GREEN_RESULTS = {"build": "LivingShadowWorldEditor build: Succeeded", "automation": "automation: 72/72 passed",
                 "scenarios": ["scenario BossFight: PASS (50/50 checks)"]}


def _finished(lsw, note, state="green", fix_rounds=0, change=True, text=None):
    o = studio.draft(note)
    wt = lsw.parent / f"wt-{o['id']}"
    o["worktree"] = str(wt)
    studiojob.ensure_worktree(o)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(wt, "config", k, v)
    if change:
        (wt / "Source/LivingShadowWorld/Boss/LSWBossTypes.h").write_text(
            text or "struct FLSWBossTuning { float RunSpeed = 600.f; float Phase2SpeedScale = 1.2f; };\n")
    o.update(state=state, fix_rounds=fix_rounds, prompt_sent="THE EXACT PROMPT",
             worker_text="Looked around.\n\nRaised phase-2 speed via Phase2SpeedScale; test Boss.Phase2Speed.",
             results=dict(GREEN_RESULTS))
    o["commit"] = studiojob.commit(o, state == "green", "x")
    studio.save(o)
    return o


def test_green_order_becomes_a_verified_lesson_and_a_dataset_row(lsw):
    studiolearn.build_map()
    o = _finished(lsw, "the boss feels too slow in phase 2")
    path = studiolearn.record(o)
    lesson = json.loads(path.read_text())
    assert path.parent.name == "lessons" and lesson["verified"] is True and lesson["lane"] == "boss"
    assert "Source/LivingShadowWorld/Boss/LSWBossTypes.h" in lesson["files_touched"]
    assert "Phase2SpeedScale" in lesson["diff"] and "72/72" in lesson["proved_by"]["automation"]
    assert lesson["why"].startswith("Raised phase-2 speed")                     # the conclusion, not the opening
    (row,) = [json.loads(x) for x in studiolearn.dataset_path().read_text().splitlines()]
    assert row["schema"] == "jarvis-studio-task/1" and "Phase2SpeedScale" in row["output"]["diff"]
    assert row["input"]["prompt"] == "THE EXACT PROMPT"                         # what the worker saw, never re-generated
    assert "not cleared" in row["provenance"]["training_use"]
    assert studiolearn.load_map()["touched"][-1]["id"] == o["id"]


def test_record_is_idempotent(lsw):
    o = _finished(lsw, "the boss feels too slow in phase 2")
    studiolearn.record(o)
    studiolearn.record(o)
    assert len(studiolearn.dataset_path().read_text().splitlines()) == 1
    assert [t["id"] for t in studiolearn.load_map()["touched"]] == [o["id"]]


@pytest.mark.parametrize("state", ["red", "failed"])
def test_a_failed_order_is_a_failure_never_a_lesson(lsw, state):
    o = _finished(lsw, "the boss feels too slow in phase 2", state=state)
    assert studiolearn.record(o).parent.name == "failures"
    assert studiolearn.lessons() == [] and not studiolearn.dataset_path().exists()
    assert studiolearn.similar("the boss feels too slow in phase 2") == []


def test_green_without_a_source_change_teaches_nothing(lsw):
    o = _finished(lsw, "the boss feels too slow in phase 2", change=False)
    assert studiolearn.record(o).parent.name == "failures" and studiolearn.lessons() == []


def test_green_with_an_unreadable_diff_is_pending_not_a_failure(lsw, monkeypatch):
    o = _finished(lsw, "the boss feels too slow in phase 2")
    with monkeypatch.context() as mp:
        mp.setattr(studiolearn, "_diff", lambda order: ([], "", "TimeoutExpired: git"))
        assert studiolearn.record(o) is None and studiolearn.failures() == []
        assert (studio.home() / "pending" / f"{o['id']}.json").exists()
    assert studiolearn.backfill() == 1 and len(studiolearn.lessons()) == 1     # retried later
    assert not (studio.home() / "pending" / f"{o['id']}.json").exists()


def test_backfill_records_a_green_order_a_crash_left_unrecorded(lsw):
    o = _finished(lsw, "the boss feels too slow in phase 2")             # green, saved, never recorded
    assert studiolearn.lessons() == []
    studio.draft("the title menu loses focus")                         # drafting runs the backfill
    assert [ls["id"] for ls in studiolearn.lessons()] == [o["id"]]


def test_a_truncated_diff_is_never_a_dataset_row(lsw, monkeypatch):
    monkeypatch.setattr(studiolearn, "MAX_DIFF", 50)
    o = _finished(lsw, "the boss feels too slow in phase 2")
    lesson = json.loads(studiolearn.record(o).read_text())
    assert lesson["diff_truncated"] is True and not studiolearn.dataset_path().exists()


def test_secrets_and_his_user_folder_are_redacted(lsw, monkeypatch):
    import os
    home = os.path.expanduser("~")
    o = _finished(lsw, "the boss feels too slow in phase 2")
    o["worker_text"] = f"Done.\n\nUsed ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuv and {home}\\LSW_wt\\x and Bearer abcdefghijklmnopqrstuvwxyz"
    lesson = json.loads(studiolearn.record(o).read_text())
    blob = json.dumps(lesson) + studiolearn.dataset_path().read_text()
    assert "sk-ant-abc" not in blob and "abcdefghijklmnopqrstuvwxyz" not in blob
    assert home.replace("\\", "\\\\") not in blob and "~" in lesson["why"]


def test_the_next_order_uses_the_lesson(lsw):
    studiolearn.record(_finished(lsw, "the warden feels too slow in phase 2"))
    nxt = studio.draft("the warden is still too slow when phase 2 starts")
    assert nxt["lessons_used"] == ["WO-1"]
    assert nxt["files"][0]["path"] == "Source/LivingShadowWorld/Boss/LSWBossTypes.h" and nxt["files"][0]["from_lesson"] == "WO-1"
    prompt = studiojob.prompt_for(nxt)
    assert "PAST VERIFIED LESSONS" in prompt and "WO-1" in prompt and "Phase2SpeedScale" in prompt
    assert studio.draft("the title menu loses focus")["lessons_used"] == []      # unrelated note: no lesson


def test_one_shared_word_is_not_a_lesson(lsw):
    studiolearn.record(_finished(lsw, "the warden feels too slow in phase 2"))
    assert studio.draft("the warden armor looks wrong")["lessons_used"] == []


def test_a_lesson_file_that_no_longer_exists_is_not_offered(lsw):
    studiolearn.record(_finished(lsw, "the warden feels too slow in phase 2", text="x"))
    lesson_path = studio.home() / "lessons" / "WO-1.json"
    d = json.loads(lesson_path.read_text())
    d["files_touched"] = ["Source/LivingShadowWorld/Boss/Gone.h"]
    lesson_path.write_text(json.dumps(d))
    nxt = studio.draft("the warden is still too slow when phase 2 starts")
    assert all(f["path"] != "Source/LivingShadowWorld/Boss/Gone.h" for f in nxt["files"])


def test_a_lesson_decides_the_lane_only_when_his_words_name_none(lsw):
    studiolearn.record(_finished(lsw, "the warden feels sluggish and lazy"))
    assert studio.pick_lane("it feels sluggish and lazy") == "core"
    assert studio.draft("it feels sluggish and lazy")["lane"] == "boss"
    soul = studio.draft("the shadow ally feels sluggish and lazy")
    assert soul["lane"] == "soul" and soul["lessons_used"] == []             # his words win; a boss lesson is not used


def test_a_broken_lesson_file_never_blocks_an_order(lsw):
    folder = studio.home() / "lessons"
    folder.mkdir(parents=True)
    (folder / "WO-9.json").write_text("[1, 2]")
    (folder / "WO-8.json").write_text(json.dumps({"id": "WO-8", "verified": True, "lane": "gone", "note": None, "files_touched": None}))
    (folder / "WO-7.json").write_text("{half")
    o = studio.draft("the boss feels too slow in phase 2")
    assert o["state"] == "draft" and o["lessons_used"] == []


def test_codebase_map_reads_the_base_and_feeds_the_prompt(lsw):
    m = studiolearn.build_map()
    boss = m["systems"]["Source/LivingShadowWorld/Boss/"]
    assert boss["lane"] == "boss" and boss["files"] == 2
    o = studio.draft("the boss feels too slow in phase 2")
    assert "CODEBASE MAP" in studiojob.prompt_for(o) and "Source/LivingShadowWorld/Boss/" in studiojob.prompt_for(o)
    assert studiolearn.load_map()["base_sha"] == m["base_sha"]


def test_the_laptop_records_his_no(lsw, queue, monkeypatch):
    m = missions.add("Studio note: the boss feels too slow in phase 2")
    assert _tick() == "parked"
    missions.answer_approval(m["id"], False, missions.get(m["id"])["approval"]["fp"])
    assert studiolearn.metrics()["rejected"] == 1
    other = missions.add("clean my downloads")
    del other
    assert len(list(studiolearn.decisions_dir().glob("*.json"))) == 1         # only studio orders are recorded


def test_metrics_count_rejections_resaying_and_second_passes(lsw):
    a = studio.draft("the boss feels too slow in phase 2")
    a["mission"] = "m-a"
    studio.save(a)
    studiolearn.note_decision("m-a", False)
    studio.draft("the boss is too slow in the second phase")                # he re-said the rejected note
    for n, rounds in (("jump feels floaty", 1), ("title menu loses focus", 0)):
        o = studio.draft(n)
        o.update(state="green", fix_rounds=rounds)
        studio.save(o)
    m = studiolearn.metrics()
    assert m["orders"] == 4 and m["rejected"] == 1 and m["resaid_after_reject"] == 1
    assert m["finished"] == 2 and m["needed_fix_round"] == 1 and not m["enough_to_tell"]
    said = studiolearn.spoken_metrics(m)
    assert "1 rejected" in said and "too few to say" in said and "a guess" in said


def test_metrics_say_whether_numbers_moved_once_there_are_enough(lsw, monkeypatch):
    monkeypatch.setattr(studiolearn, "record", lambda o: None)
    for i in range(20):
        o = studio.draft(f"tune the lunge number {i} please")
        o.update(state="green", fix_rounds=1 if i < 10 else 0)
        studio.save(o)
    m = studiolearn.metrics()
    assert m["enough_to_tell"] and m["fix_round_rate"]["first_half"] == 1.0 and m["fix_round_rate"]["second_half"] == 0.0
    assert "from 100% of the early orders to 0%" in studiolearn.spoken_metrics(m)


def test_job_records_the_lesson_at_the_end(lsw, monkeypatch):
    monkeypatch.setattr(studiojob, "ensure_worktree", lambda o: None)
    monkeypatch.setattr(studio, "note_pid", lambda oid, pid: None)
    kept = []
    monkeypatch.setattr(studiolearn, "record", lambda o: kept.append((o["id"], o["state"])))
    o = studio.draft("the boss feels too slow in phase 2")
    studio.set_state(o, "approved")
    green = {"ok": True, "stage": "tests", "digest": "", "results": {"build": "Succeeded", "automation": "automation: 1/1", "scenarios": []}}
    studiojob.run(o["id"], worker=lambda *a: {"ok": True, "text": "done", "cost": 0}, verifier=lambda *a: green, committer=lambda *a: "c1")
    assert studio.load(o["id"])["prompt_sent"].startswith("You are a senior Unreal")
    o2 = studio.draft("the jump feels floaty")
    studio.set_state(o2, "approved")
    studiojob.run(o2["id"], worker=lambda *a: {"ok": False, "text": "no login", "cost": 0}, committer=lambda *a: "")
    assert kept[0] == ("WO-1", "green") and kept[-1] == ("WO-2", "failed")   # (the 2nd draft's backfill retries WO-1)


@pytest.mark.parametrize("line", ["How is the studio learning?", "what has the studio learned", "is the studio getting better"])
def test_learning_question_reaches_the_metrics(monkeypatch, line):
    from core import authz
    from tools import gamestudio
    monkeypatch.setattr(authz, "is_owner", lambda: True)
    monkeypatch.setattr(gamestudio, "_owner", lambda: True)
    monkeypatch.setattr(studiolearn, "spoken_metrics", lambda m=None: "metrics")
    assert studio.handle(line) == "metrics"


def test_an_order_named_improve_is_still_a_status_question(lsw, monkeypatch):
    from tools import gamestudio
    monkeypatch.setattr(gamestudio, "_owner", lambda: True)
    studio.draft("improve the camera when locking on")
    assert "Work order 1" in gamestudio.studio_status("improve camera")


def test_local_coder_prompt_carries_similar_lessons_but_never_the_answer(lsw):
    from scripts import local_coder_trial as trial
    studiolearn.record(_finished(lsw, "the warden feels too slow in phase 2"))
    _git(lsw, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "warden too slow in phase 2 again")
    p = trial.task_prompt(lsw, "HEAD", lessons=True)
    assert "PAST VERIFIED LESSONS" in p["prompt"] and p["lessons_used"] == ["WO-1"]
    assert "PAST VERIFIED" not in trial.task_prompt(lsw, "HEAD")["prompt"]
    # a commit that IS the lesson's change: the lesson would be the answer, so it is withheld
    (lsw / "Source/LivingShadowWorld/Boss/LSWBossTypes.h").write_text(
        "struct FLSWBossTuning { float RunSpeed = 600.f; float Phase2SpeedScale = 1.2f; };\n")
    _git(lsw, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "warden too slow in phase 2 speed")
    assert trial.task_prompt(lsw, "HEAD", lessons=True)["lessons_used"] == []
    assert subprocess.run(["git", "-C", str(lsw), "status", "--porcelain"], capture_output=True, text=True).stdout == ""


def test_a_retried_order_that_turns_green_still_becomes_a_lesson(lsw):
    o = _finished(lsw, "the boss feels too slow in phase 2", state="failed")
    assert studiolearn.record(o).parent.name == "failures"
    o["state"] = "green"
    studio.save(o)
    assert studiolearn.record(o).parent.name == "lessons"
    assert studiolearn.failures() == [] and len(list((studio.home() / "attempts").glob("WO-1-*.json"))) == 1
