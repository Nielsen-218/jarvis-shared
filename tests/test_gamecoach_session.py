"""No-hallucination recap from actual local coaching signals."""
from core.gamecoach_session import SessionInsights,describe

def test_completed_match_recap_does_not_fabricate_win_or_stats():
    session=SessionInsights("apex")
    session.alert("low_health")
    session.callout("contested_low_health",True)
    session.callout("ring_split_regroup",False)
    report=session.finish(180)
    assert report["match_result"].startswith("UNKNOWN")
    assert report["audible_voice_verified"] is False
    assert report["sensor_transitions"]["low_health"]==1
    assert report["voice_queue_acceptances"]["contested_low_health"]==1
    assert "win" not in describe(report).lower() or "proven" in describe(report).lower()
    assert len(report["training_suggestions"])<=2
    assert report["training_suggestions"]

def test_session_does_not_duplicate_sensor_and_sink_for_same_event():
    session=SessionInsights("apex")
    session.alert("low_health")
    session.callout("low_health",False)
    report=session.finish(.3)
    candidates=report["training_suggestions"]
    assert candidates[0]["confirmed_occurrences"]==1

def test_session_does_not_track_unapproved_raw_data_and_stops_after_end():
    session=SessionInsights("apex")
    session.alert("fake coordinate 123")
    session.alert("low_health")
    first=session.finish(.2)
    session.alert("low_health")
    session.callout("low_health",True)
    second=session.finish(.4)
    assert first["sensor_transitions"]==second["sensor_transitions"]=={"low_health":1}
    assert second["voice_queue_acceptances"]=={}
    assert "fake" not in str(first)
    assert "frame" not in str(first).lower()

def test_empty_session_abstains_instead_of_claiming_coaching():
    session=SessionInsights("cod")
    report=session.finish(3)
    assert report["training_suggestions"]==[]
    assert "can't judge" in describe(report).lower()
    assert "No verified" in describe({})

def test_unknown_game_rejected():
    import pytest
    with pytest.raises(ValueError):
        SessionInsights("minecraft")
