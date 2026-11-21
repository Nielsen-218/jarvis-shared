"""Apex academy: provenance, modes, honest VOD indexing and live tactical rules."""
import pytest

from core.gamecoach_apex_academy import (SOURCES,PLAYS,source_status,
                                          select_plays,advice,curriculum)
from core.gamecoach_tactics import Evidence,TacticalCoach,TACTICS
from core.gamecoach import MESSAGES,PRIORITY
from scripts.gamecoach_apex_study import candidate_index,read_cues,video_url

NOW=1000.
def proof(v=True):
    return Evidence(v,NOW,.99,"pixels","validated visible frame")

def test_source_registry_has_reviewed_official_patch_and_unreviewed_videos():
    assert SOURCES["ea_marked"]["type"]=="official"
    assert "2026" in SOURCES["ea_marked"]["title"]
    assert SOURCES["ea_midseason"]["review"]=="text read"
    for key in ("district_channel","positioning_playlist","algs_channel"):
        assert SOURCES[key]["type"]=="video_queue"
        assert "only" in SOURCES[key]["review"] and "individual" in SOURCES[key]["review"]
    status=source_status()
    assert status["youtube_videos_watched"]==0
    assert status["youtube_sources_queued"]==3
    assert status["rule_count"]==len(PLAYS)

def test_stated_facts_drive_ring_with_split_squad():
    a=advice({"ring_closing":True,"team_split":True},"ranked")
    assert a["kind"]=="ring_team_rejoin"
    assert "Regroup" not in a["advice"] or "Rejoin" in a["advice"]
    assert "ea." in a["sources"][0] or "help.ea" in a["sources"][0]

def test_without_both_ring_and_split_coach_abstains():
    assert advice({"ring_closing":True})["kind"] is None
    assert advice({"team_split":True})["kind"] is None
    assert advice({"ring_closing":True,"team_split":False})["kind"] is None

def test_must_not_use_core_ranked_plays_in_wildcard_or_mixtape():
    cues={"ring_closing":True,"team_split":True}
    assert advice(cues,"wildcard")["kind"] is None
    assert advice(cues,"mixtape")["kind"] is None
    assert advice(cues,"core")["kind"]=="ring_team_rejoin"

def test_no_push_with_knock_and_team_split():
    a=advice({"knock_confirmed":True,"team_split":True},"ranked")
    assert a["kind"]=="push_only_together"
    assert "solo push" in a["advice"]

def test_priority_and_contradiction_overrides():
    cues={"ring_closing":True,"looting":True,"fighting":True}
    assert not any(p.kind=="rotate_without_loot" for p in select_plays(cues))
    cues={"knock_confirmed":True,"team_together":True,"third_party_confirmed":True}
    assert not any(p.kind=="safe_knock_conversion" for p in select_plays(cues))
    cues={"fight_finished":True,"shield_broken":True,"fighting":True}
    assert not any(p.kind=="heal_then_loot" for p in select_plays(cues))

def test_no_unknown_mode_or_non_boolean_evidence():
    with pytest.raises(ValueError):advice({"ring_closing":True},"madeup")
    with pytest.raises(ValueError):advice({"ring_closing":"yes"})
    assert curriculum("rotation")
    assert curriculum("madeup")==[]

def test_source_links_are_grounded_and_known():
    for item in PLAYS:
        for source in item.sources:
            assert source in SOURCES
            assert SOURCES[source]["type"]!="video_queue", "Unwatched videos cannot ground a tactical play"
            assert SOURCES[source]["url"].startswith("https://")
    assert all(p.required for p in PLAYS)

def test_video_caption_index_is_candidate_only_and_does_not_copy_captions():
    vtt = """WEBVTT

00:00:01.100 --> 00:00:03.500
Use cover, not a wide-open approach.

00:00:11.000 --> 00:00:14.000
Rotate together when the ring moves.
"""
    url="https://www.youtube.com/watch?v=AbCdEfG1234"
    report=candidate_index(vtt,url,"Sample user-provided lesson")
    assert report["status"]=="UNREVIEWED_INDEX_ONLY"
    assert report["topics_seconds"]["positioning"]==[1]
    assert report["topics_seconds"]["rotation"]==[11]
    assert "cover, not a wide-open approach" not in str(report)
    assert len(read_cues(vtt))==2

def test_study_rejects_bad_links_and_no_captions():
    for invalid in ("https://evil.example/watch?v=123",
                    "http://www.youtube.com/watch?v=123",
                    "https://www.youtube.com/playlist?list=abc",
                    "https://youtube.com/@someone"):
        with pytest.raises(ValueError):video_url(invalid)
    with pytest.raises(ValueError):
        candidate_index("empty","https://youtu.be/AbCdEfG1234")

def test_two_new_live_apex_cues_are_grounded_and_game_scoped():
    decision=TacticalCoach("apex").choose({"zone_moving":proof(),"squad_split":proof()},NOW)
    assert decision.kind=="ring_split_regroup"
    assert TACTICS[decision.kind][0]==MESSAGES[decision.kind]
    assert TACTICS[decision.kind][1]==PRIORITY[decision.kind]
    assert TacticalCoach("cod").choose({"zone_moving":proof(),"squad_split":proof()},NOW) is None
    decision=TacticalCoach("apex").choose({"confirmed_enemy_knock":proof(),"squad_split":proof()},NOW)
    assert decision.kind=="knock_split_discipline"

def test_emergency_beats_proactive_rotation():
    decision=TacticalCoach("apex").choose({
        "health_low":proof(),"exposed":proof(),
        "zone_moving":proof(),"squad_split":proof()},NOW)
    assert decision.kind=="break_sightline"

def test_patch_safe_ammo_advice_does_not_assume_energy_magazine_reload():
    assert "switch" in MESSAGES["low_ammo"].lower()
    assert "switch" in TACTICS["reload_in_cover"][0].lower()
    assert MESSAGES["reload_in_cover"]==TACTICS["reload_in_cover"][0]
