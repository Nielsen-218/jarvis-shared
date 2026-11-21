"""Live misses are re-studied decide-only: honest outcomes, never executed, never promoted, studied once per miss."""
from core import gateway, misslessons, storage
from core.gateway import Decision, Intent


def _decision(*intents, covers=True):
    return Decision(list(intents), "advanced", covers=covers)


def test_outcomes_are_judged_without_running_anything(monkeypatch):
    ran = []
    monkeypatch.setattr(gateway, "execute", lambda *a, **k: ran.append(a))
    ok = _decision(Intent("actions", "open paint", steps=[{"tool": "open_app", "arguments": {"app": "paint"}}]))
    assert misslessons.judge(ok)[0] == "understood"
    assert misslessons.judge(_decision(Intent("clarify", question="Which folder?")))[0] == "unclear"
    assert misslessons.judge(_decision(Intent("actions", steps=[{"tool": "open_app", "arguments": {"app": "paint"}}]), covers=False))[0] == "unclear"
    assert misslessons.judge(_decision(Intent("chat", "no tool")))[0] == "unsupported"
    assert misslessons.judge(_decision(Intent("actions", steps=[{"tool": "open_app", "arguments": {}}])))[0] == "unsupported"
    assert misslessons.judge(None)[0] == "unsupported"
    assert ran == []


def test_review_studies_each_new_miss_once_and_labels_it_experimental(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    misses = [{"at": 100.0, "said": "draw a circle in paint", "reason": "step not verified"},
              {"at": 101.0, "said": "Draw a circle in Paint!", "reason": "again"},
              {"at": 102.0, "said": "do the thing", "reason": "part of the request was not understood"}]
    asked = []

    def decide(said, cancelled=None):
        asked.append(said)
        if "circle" in said.casefold():
            return _decision(Intent("actions", steps=[{"tool": "use_app", "arguments": {"app": "paint", "goal": "draw a circle"}}]))
        return _decision(Intent("clarify", question="Which thing?"))

    rows = misslessons.review(misses=misses, decide=decide)
    assert [r["outcome"] for r in rows] == ["unclear", "understood"] and len(asked) == 2      # the duplicate sentence once
    assert {r["said"]: r["status"] for r in rows}["Draw a circle in Paint!"] == "EXPERIMENTAL"
    assert misslessons.review(misses=misses, decide=decide) == []                           # already studied since the miss
    later = misses + [{"at": 10**12, "said": "do the thing", "reason": "again"}]
    again = misslessons.review(misses=later, decide=decide)
    assert [r["said"] for r in again] == ["do the thing"] and again[0]["tries"] == 2
    line = misslessons.summary()
    assert "understand 1" in line and "do the thing" in line and "MASTERED" not in line


def test_review_is_bounded_and_survives_a_failing_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    misses = [{"at": float(i), "said": f"task number {i}"} for i in range(10)]

    def boom(said, cancelled=None):
        raise RuntimeError("model down")

    rows = misslessons.review(limit=3, misses=misses, decide=boom)
    assert len(rows) == 3 and all(r["outcome"] == "unclear" and "RuntimeError" in r["why"] for r in rows)


def test_jarvis_own_briefing_turn_is_not_a_miss(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    gateway.record_miss("[Automatic morning briefing: the user just sat down at the PC]", "part of the request was not understood")
    gateway.record_miss("close it", "step not verified")
    assert [m["said"] for m in gateway.recent_misses()] == ["close it"]


def test_misses_are_replayed_only_in_a_gap_and_never_during_a_game(monkeypatch, tmp_path):
    """Background model work (core/inferencegate): a replay waits for a gap between his turns, holds for a game, and a
    replay his turn cut into is not written as a lesson (it is studied another time)."""
    import threading
    from core import inferencegate
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setattr(inferencegate, "POLL_S", 0.05)
    monkeypatch.setattr(misslessons, "GATE_WAIT_S", 0.3)
    misses = [{"at": 1.0, "said": "draw a circle in paint"}, {"at": 2.0, "said": "do the thing"}]
    asked = []

    def decide(said, cancelled=None):
        asked.append(said)
        return _decision(Intent("clarify", question="Which thing?"))

    monkeypatch.setattr(inferencegate, "GAMING", lambda: True)
    assert misslessons.review(misses=misses, decide=decide) == [] and asked == []       # a game: nothing replayed
    monkeypatch.setattr(inferencegate, "GAMING", lambda: False)
    with inferencegate.foreground():
        assert misslessons.review(misses=misses, decide=decide) == [] and asked == []   # his turn: nothing replayed
    started, release = threading.Event(), threading.Event()

    def cut_into(said, cancelled=None):
        asked.append(said)
        started.set()
        release.wait(2)
        return _decision(Intent("clarify", question="Which thing?"))
    out = []
    worker = threading.Thread(target=lambda: out.append(misslessons.review(misses=misses, decide=cut_into)))
    worker.start()
    assert started.wait(2)
    with inferencegate.foreground():                         # he speaks while the replay runs
        release.set()
        worker.join(2)
    assert out == [[]] and misslessons.lessons() == {}       # the replay finished, but no lesson is written from it


# ------------------------------------------------------------------------------------------------ later nights (2026-11-13)
NIGHT = 24 * 3600.0


def test_a_miss_that_keeps_failing_is_another_try_each_night_and_reaches_the_teacher_gate(monkeypatch, tmp_path):
    """Before: a sentence was studied once and never again until it was missed anew, so tries stayed 1 and teach() (tries>=2)
    could not be reached."""
    monkeypatch.setattr(storage, "DATA", tmp_path)
    from core import teacher
    misses = [{"at": 100.0, "said": "do the thing", "reason": "part of the request was not understood"}]
    fail = lambda said, cancelled=None: _decision(Intent("clarify", question="Which thing?"))
    t0 = 1000.0
    assert [r["tries"] for r in misslessons.review(misses=misses, decide=fail, now=t0)] == [1]
    assert misslessons.review(misses=misses, decide=fail, now=t0 + 3600) == []                 # the same night: nothing
    asked = []
    monkeypatch.setattr(teacher, "can_help", lambda wid: asked.append(wid) or (False, "closed"))   # never a model in a test
    assert misslessons.teach(limit=2, decide=fail) == [] and asked == []                       # one try is not enough
    assert [r["tries"] for r in misslessons.review(misses=misses, decide=fail, now=t0 + NIGHT)] == [2]   # a later night
    misslessons.teach(limit=2, decide=fail)
    assert asked == [misslessons.lesson_id("do the thing")]                                   # now it reaches the gate


def test_a_failed_replay_on_a_later_night_counts_as_a_try(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    misses = [{"at": 100.0, "said": "do the thing"}]

    def boom(said, cancelled=None):
        raise RuntimeError("model down")
    misslessons.review(misses=misses, decide=boom, now=1000.0)
    again = misslessons.review(misses=misses, decide=boom, now=1000.0 + NIGHT)
    assert [r["tries"] for r in again] == [2] and "RuntimeError" in again[0]["why"]


def test_an_item_that_now_passes_is_closed_as_learned_by_fix_and_not_replayed(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    misses = [{"at": 100.0, "said": "draw a circle in paint"}]
    stuck = lambda said, cancelled=None: _decision(Intent("clarify", question="Where?"))
    fixed = lambda said, cancelled=None: _decision(Intent("actions", steps=[{"tool": "use_app", "arguments": {"app": "paint", "goal": "draw a circle"}}]))
    misslessons.review(misses=misses, decide=stuck, now=1000.0)
    closed = misslessons.review(misses=misses, decide=fixed, now=1000.0 + NIGHT)
    assert closed[0]["outcome"] == "understood" and closed[0]["closed_as"] == "learned-by-fix" and closed[0]["tries"] == 2
    assert misslessons.review(misses=misses, decide=stuck, now=1000.0 + 5 * NIGHT) == []      # closed: left alone


def test_a_recovered_miss_is_not_studied_and_a_hopeless_one_stops_at_the_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    stuck = lambda said, cancelled=None: _decision(Intent("clarify", question="Which?"))
    assert misslessons.review(misses=[{"at": 1.0, "said": "x y z", "recovered_at": 5.0}], decide=stuck, now=9.0) == []
    misses = [{"at": 1.0, "said": "do the thing"}]
    for night in range(misslessons.MAX_TRIES + 3):
        misslessons.review(misses=misses, decide=stuck, now=1000.0 + night * NIGHT)
    assert misslessons.lessons()[misslessons._key("do the thing")]["tries"] == misslessons.MAX_TRIES
