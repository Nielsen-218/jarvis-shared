"""Actual recorded behavior across restarts, with isolated atomic storage."""
import copy
from datetime import datetime, timezone
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import companion_life as life
from core.companion_home import default_home, validate_home

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc).timestamp()
DAY = "2026-10-24"
SURFACE = "taskbar:0:0:bottom"


def event(path, kind, **kwargs):
    return life.record_event(kind, now=NOW, path=path, **kwargs)


def greeted(path):
    return event(path, "greeting")["snapshot"]


def test_defaults_are_fresh_and_missing_is_readonly(tmp_path):
    path = tmp_path / "life.json"
    s = life.load_life(path)
    s["counts"]["play"] = 10
    assert life.load_life(path) == life.default_life()
    assert not path.exists()


def test_env_root_and_explicit_path_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COMPANION_DATA_DIR", str(tmp_path / "first"))
    event(None, "hold")
    assert life.load_life()["counts"]["hold"] == 1
    assert (tmp_path / "first" / "companion-life.json").exists()
    assert life.load_life(tmp_path / "explicit.json")["counts"]["hold"] == 0
    monkeypatch.setenv("JARVIS_COMPANION_DATA_DIR", str(tmp_path / "second"))
    assert life.load_life()["counts"]["hold"] == 0


def test_interactions_and_correction_survive_reload(tmp_path):
    path = tmp_path / "life.json"
    good = event(path, "play", action="play", surface_id=SURFACE)
    assert good["mood"] == "playful"
    assert good["snapshot"]["last_good"]["kind"] == "play"
    corrected = event(path, "correction", action="play", surface_id=SURFACE)
    assert corrected["mood"] == "neutral"
    saved = life.load_life(path)
    assert saved == corrected["snapshot"]
    assert saved["last_good"]["kind"] == "play"
    assert saved["last_correction"]["kind"] == "correction"
    assert saved["surfaces"][SURFACE]["corrections"] == 1
    assert saved["actions"]["play"]["corrections"] == 1


def test_derived_confidence_curiosity_follow_facts(tmp_path):
    path = tmp_path / "life.json"
    initial = life.traits(life.default_life())
    for _ in range(10):
        played = event(path, "play")
    assert played["traits"]["confidence"] > initial["confidence"]
    assert played["traits"]["curiosity"] > initial["curiosity"]
    for _ in range(10):
        failed = event(path, "skill_fail")
    assert failed["traits"]["confidence"] < played["traits"]["confidence"]
    corrected = event(path, "correction")
    assert corrected["traits"]["curiosity"] < failed["traits"]["curiosity"]
    assert all(0 <= v <= 1 for v in corrected["traits"].values())


def test_unlocks_only_from_actual_events_and_home_compatible(tmp_path):
    path = tmp_path / "life.json"
    s = greeted(path)
    for seed in range(100):
        assert life.director_choice(s, NOW + seed, seed=seed)["action"] != "secret_shuffle"
    assert life.load_life(path)["counts"]["play"] == 0
    for _ in range(5):
        hold = event(path, "hold")
    assert hold["new_unlocks"][0]["id"] == "life-gentle-hands"
    assert event(path, "hold")["new_unlocks"] == []
    for _ in range(10):
        result = event(path, "play")
    assert result["secret_actions"] == []  # play alone never sufficient
    for _ in range(3):
        result = event(path, "skill_success")
    assert result["secret_actions"] == ["secret_shuffle"]
    assert result["new_unlocks"][0]["id"] == "life-secret-shuffle"
    for _ in range(2):
        result = event(path, "skill_success")
    assert len(result["unlocked"]) == 3
    home = default_home()
    home["inventory"] = result["unlocked"]
    assert validate_home(home) == home
    before = path.read_bytes()
    choices = {life.director_choice(life.load_life(path), NOW + 300, seed=seed)["action"] for seed in range(200)}
    assert "secret_shuffle" in choices
    assert path.read_bytes() == before


def test_failures_and_corrections_never_unlock(tmp_path):
    path = tmp_path / "life.json"
    for _ in range(12):
        event(path, "skill_fail")
        result = event(path, "correction", action="play")
    assert result["unlocked"] == result["secret_actions"] == []


def test_common_shuffle_is_not_an_earned_secret(tmp_path):
    s = greeted(tmp_path / "life.json")
    choices = [life.director_choice(s, NOW, seed=i) for i in range(200)]
    common = [c for c in choices if c["action"] == "shuffle"]
    assert common
    assert all(c["reason"] == "learned_preferences" and c["mood"] == "playful" for c in common)
    assert all(c["action"] != "secret_shuffle" and c["reason"] != "earned_secret" for c in choices)


def test_secret_corrections_are_independent_and_persisted(tmp_path):
    path = tmp_path / "life.json"
    greeted(path)
    for _ in range(10):
        event(path, "play")
    for _ in range(3):
        event(path, "skill_success")
    s = life.load_life(path)
    choices = [life.director_choice(s, NOW, seed=i) for i in range(200)]
    secret = [c for c in choices if c["action"] == "secret_shuffle"]
    assert secret and all(c["reason"] == "earned_secret" and c["mood"] == "playful" for c in secret)
    event(path, "correction", action="secret_shuffle")
    s = life.load_life(path)
    choices = {life.director_choice(s, NOW, seed=i)["action"] for i in range(200)}
    assert "secret_shuffle" not in choices and "shuffle" in choices
    # Separate common-action correction must not revoke the earned unlock.
    event(path, "correction", action="shuffle")
    s = life.record_event("greeting", now=NOW + life.CORRECTION_COOLDOWN, path=path)["snapshot"]
    choices = {life.director_choice(s, NOW + life.CORRECTION_COOLDOWN, seed=i)["action"] for i in range(400)}
    assert "secret_shuffle" in choices


def test_surface_favorite_is_available_and_respects_correction(tmp_path):
    path = tmp_path / "life.json"
    other = "window:1234"
    for _ in range(3):
        event(path, "surface", surface_id=SURFACE)
    event(path, "surface", surface_id=other)
    s = greeted(path)
    both = [SURFACE, other]
    assert life.director_choice(s, NOW, available_surfaces=both)["surface_id"] == SURFACE
    assert life.director_choice(s, NOW, available_surfaces=[other])["surface_id"] == other
    assert life.director_choice(s, NOW, available_surfaces=[])["surface_id"] is None
    event(path, "correction", surface_id=SURFACE)
    assert life.director_choice(life.load_life(path), NOW, available_surfaces=both)["surface_id"] == other
    # Corrections persist after cooldown as a lower score, not permanent exile.
    for _ in range(8):
        life.record_event("surface", now=NOW + 90000, path=path, surface_id=SURFACE)
    assert life.director_choice(life.load_life(path), NOW + 90000, available_surfaces=both)["surface_id"] == SURFACE


@pytest.mark.parametrize("surface", ["folder:0123456789abcdef", "window:123", "taskbar:-1920:0:top", "search:taskbar:0:0:bottom"])
def test_existing_habitat_id_formats(tmp_path, surface):
    assert event(tmp_path / "life.json", "surface", surface_id=surface)["snapshot"]["surfaces"][surface]["good"] == 1


def test_action_correction_changes_selection_across_restart(tmp_path):
    path = tmp_path / "life.json"
    s = greeted(path)
    assert "curious" in {life.director_choice(s, NOW, seed=i)["action"] for i in range(200)}
    event(path, "correction", action="curious")
    s = life.load_life(path)
    assert "curious" not in {life.director_choice(s, NOW, seed=i)["action"] for i in range(200)}
    # Daily greeting wins on the following day; acknowledge it before testing
    # the underlying idle preference after its correction cooldown expires.
    s = life.record_event("greeting", now=NOW + life.CORRECTION_COOLDOWN, path=path)["snapshot"]
    assert "curious" in {life.director_choice(s, NOW + life.CORRECTION_COOLDOWN, seed=i)["action"] for i in range(300)}


def test_global_correction_suppresses_daily_prompts(tmp_path):
    path = tmp_path / "life.json"
    s = event(path, "correction")["snapshot"]
    assert life.director_choice(s, NOW)["reason"] == "recent_correction"
    assert life.director_choice(s, NOW)["record"] is None
    assert life.director_choice(s, NOW + life.CORRECTION_COOLDOWN)["reason"] == "daily_greeting"


def test_daily_greeting_only_acknowledged_after_actual_event(tmp_path):
    path = tmp_path / "life.json"
    s = life.load_life(path)
    decision = life.director_choice(s, NOW)
    assert decision["reason"] == "daily_greeting"
    assert decision == life.director_choice(s, NOW)
    assert not path.exists()
    acknowledged = life.record_event(**decision["record"], now=NOW, path=path)["snapshot"]
    assert life.director_choice(acknowledged, NOW)["reason"] != "daily_greeting"
    assert event(path, "greeting")["snapshot"]["counts"]["greeting"] == 1
    assert life.director_choice(life.load_life(path), NOW + 86400)["reason"] == "daily_greeting"


def test_reuses_home_routines_and_daily_checkpoint(tmp_path):
    path = tmp_path / "life.json"
    for _ in range(3):
        event(path, "hold")
    s = greeted(path)
    home = default_home()
    home["routines"] = {"enabled": True, "entries": [
        {"time": "07:00", "room": "kitchen", "activity": "cook"},
        {"time": "22:00", "room": "bedroom", "activity": "sleep"}]}
    original = copy.deepcopy(home)
    d = life.director_choice(s, NOW, minute=23 * 60, home=home)
    assert d["home"] == {"room": "bedroom", "activity": "sleep"}
    assert home == original
    s = life.record_event(**d["record"], now=NOW, path=path)["snapshot"]
    assert s["daily"][0]["routines"] == ["22:00"]
    d = life.director_choice(s, NOW, minute=23 * 60, home=home)
    assert d["reason"] == "daily_checkpoint"
    s = life.record_event(**d["record"], now=NOW, path=path)["snapshot"]
    assert life.director_choice(s, NOW, minute=23 * 60, home=home)["record"] is None
    assert life.director_choice(s, NOW, minute=6 * 60, home=home)["home"] is None


def test_quiet_busy_and_reduced_motion(tmp_path):
    s = life.default_life()
    for flag in ("quiet", "busy"):
        d = life.director_choice(s, NOW, **{flag: True})
        assert d["action"] == "idle" and d["record"] is None and d["surface_id"] is None
    assert life.director_choice(s, NOW, reduced_motion=True)["action"] == "idle"
    s = greeted(tmp_path / "life.json")
    assert life.director_choice(s, NOW, reduced_motion=True)["reason"] == "reduced_motion"


def test_director_is_deterministic_and_pure(tmp_path):
    s = greeted(tmp_path / "life.json")
    before = copy.deepcopy(s)
    assert life.director_choice(s, NOW, seed="fixed") == life.director_choice(s, NOW, seed="fixed")
    assert s == before


def test_checkpoint_does_not_invent_todays_interactions(tmp_path):
    path = tmp_path / "life.json"
    for _ in range(3):
        event(path, "hold")
    s = life.record_event("greeting", now=NOW + 86400, path=path)["snapshot"]
    assert life.director_choice(s, NOW + 86400, minute=20 * 60)["reason"] != "daily_checkpoint"


def test_routine_acknowledgement_is_idempotent(tmp_path):
    path = tmp_path / "life.json"
    first = event(path, "routine", routine_time="07:00")
    second = event(path, "routine", routine_time="07:00")
    assert first["snapshot"] == second["snapshot"]
    assert second["snapshot"]["counts"]["routine"] == 1


@pytest.mark.parametrize("kwargs", [
    {"kind": "unknown"}, {"kind": "play", "now": float("nan")},
    {"kind": "play", "now": float("inf")}, {"kind": "play", "action": "delete_file"},
    {"kind": "play", "surface_id": "C:/secret/file"},
    {"kind": "play", "surface_id": "private window title"},
    {"kind": "routine", "routine_time": "24:00"}, {"kind": "routine"},
    {"kind": "play", "routine_time": "07:00"}, {"kind": "greeting", "day": "2026-02-30"},
])
def test_invalid_events_leave_disk_unchanged(tmp_path, kwargs):
    path = tmp_path / "life.json"
    event(path, "hold")
    before = path.read_bytes()
    with pytest.raises(life.LifeError):
        life.record_event(path=path, **({"now": NOW} | kwargs))
    assert path.read_bytes() == before


def test_transcripts_and_arbitrary_metadata_not_accepted(tmp_path):
    with pytest.raises(TypeError):
        life.record_event("play", transcript="secret", path=tmp_path / "life.json")
    s = life.default_life()
    s["transcript"] = "secret"
    with pytest.raises(life.LifeError):
        life.validate_life(s)


def test_backdated_clock_does_not_count(tmp_path):
    path = tmp_path / "life.json"
    event(path, "hold")
    with pytest.raises(life.LifeError):
        life.record_event("play", now=NOW - 1, path=path)
    assert life.load_life(path)["counts"]["play"] == 0
    with pytest.raises(life.LifeError):
        life.director_choice(life.load_life(path), NOW - 1)


def test_bounded_history_surfaces_and_daily(tmp_path):
    path = tmp_path / "life.json"
    for i in range(80):
        life.record_event("surface", now=NOW + i, surface_id=f"window:{i}", path=path)
    s = life.load_life(path)
    assert len(s["history"]) == life.MAX_HISTORY
    assert len(s["surfaces"]) == life.MAX_SURFACES
    assert s["counts"]["surface"] == 80
    assert "window:0" not in s["surfaces"]
    for i in range(20):
        life.record_event("greeting", now=NOW + i * 86400 + 100, path=path)
    assert len(life.load_life(path)["daily"]) == life.MAX_DAYS


def test_counters_saturate(tmp_path):
    path = tmp_path / "life.json"
    s = life.default_life()
    s["counts"]["hold"] = life.MAX_COUNT
    path.write_text(json.dumps(s), encoding="utf-8")
    assert event(path, "hold")["snapshot"]["counts"]["hold"] == life.MAX_COUNT


@pytest.mark.parametrize("payload", ["{broken", "null", "[]", "x" * (life.MAX_BYTES + 1)],
                         ids=["broken", "null", "list", "oversized"])
def test_corrupt_load_is_readonly(tmp_path, payload):
    path = tmp_path / "life.json"
    path.write_text(payload, encoding="utf-8")
    assert life.load_life(path) == life.default_life()
    assert path.read_text(encoding="utf-8") == payload


def test_atomic_failure_preserves_file_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "life.json"
    event(path, "hold")
    before = path.read_bytes()
    def fail(*args):
        raise OSError("simulated replacement failure")
    monkeypatch.setattr(life.os, "replace", fail)
    with pytest.raises(OSError):
        event(path, "play")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_threaded_events_not_lost(tmp_path):
    path = tmp_path / "life.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: event(path, "hold"), range(40)))
    assert life.load_life(path)["counts"]["hold"] == 40


def test_alias_and_no_home_write(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COMPANION_DATA_DIR", str(tmp_path))
    assert life.onEvent is life.record_event
    before = {p.name for p in tmp_path.iterdir()}
    life.onEvent("hold", now=NOW)
    assert {p.name for p in tmp_path.iterdir()} - before == {"companion-life.json"}


def test_failure_mood_and_same_timestamp_recovery(tmp_path):
    path = tmp_path / "life.json"
    assert event(path, "skill_success")["mood"] == "proud"
    assert event(path, "skill_fail")["mood"] == "neutral"
    assert event(path, "correction")["mood"] == "neutral"
    assert event(path, "play")["mood"] == "playful"


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(schema=True),
    lambda s: s["counts"].update(play=-1),
    lambda s: s["counts"].update(play=life.MAX_COUNT + 1),
    lambda s: s["counts"].update(play=True),
    lambda s: s.update(last_at=float("inf")),
    lambda s: s["history"].append({"kind": "play", "at": 10, "action": None, "surface_id": None}),
    lambda s: s["actions"].update(private_skill={}),
    lambda s: s["surfaces"].update(private_title={}),
    lambda s: s["daily"].append({"day": DAY, "greeting": 1, "checkpoint": False, "routines": []}),
    lambda s: s.update(last_good={"kind": "correction", "at": 0, "action": None, "surface_id": None}),
])
def test_schema_rejects_malformed_nested_state(mutate):
    s = life.default_life()
    mutate(s)
    with pytest.raises(life.LifeError):
        life.validate_life(s)


@pytest.mark.parametrize("kwargs", [
    {"now": float("nan")}, {"now": -1}, {"seed": {}}, {"seed": "x" * 129},
    {"day": "2026-02-30"}, {"minute": 1440}, {"minute": True},
    {"available_surfaces": ["user transcript"]}, {"quiet": "false"},
    {"home": {"room": "secret"}},
])
def test_director_rejects_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        life.director_choice(life.default_life(), **({"now": NOW} | kwargs))
