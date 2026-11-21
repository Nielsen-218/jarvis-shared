"""Nolagin tactical decision / player-learning regression suite."""
import json

from core.gamecoach import MESSAGES, PRIORITY
from core.gamecoach_tactics import Evidence, TacticalCoach
from core.gamecoach_training import build_profile, CATEGORIES, DRILLS
from scripts.gamecoach_tactical_replay import evaluate

NOW=1000.

def e(value, confidence=.96, source="pixels", age=0., proof="visible in test frame"):
    return Evidence(value,NOW-age,confidence,source,proof)

def coach(signals,game="apex",weights=None):
    return TacticalCoach(game,learning_weights=weights).choose(signals,NOW)

def test_every_tactical_call_registered_with_voice_priority():
    from core.gamecoach_tactics import TACTICS
    assert all(k in MESSAGES and k in PRIORITY for k in TACTICS)
    assert all(v[0]==MESSAGES[k] and v[1]==PRIORITY[k] for k,v in TACTICS.items())

def test_exposed_low_health_beats_lesser_advice():
    d=coach({"health_low":e(True),"exposed":e(True),
             "shields_low":e(True),"cover_visible":e(True)})
    assert d.kind=="break_sightline" and d.priority==6
    assert "health_low" in d.evidence[0]

def test_alone_against_two_visible_enemies():
    d=coach({"visible_enemy_count":e(2),"visible_allies_count":e(0)})
    assert d.kind=="retreat_outnumbered"
    assert "one-on-one" in d.text

def test_visible_two_angle_threat_prioritized():
    d=coach({"enemy_flank_visible":e(True),"enemy_visible":e(True),
             "squad_split":e(True)})
    assert d.kind=="avoid_pinch"

def test_dangerous_open_revive():
    d=coach({"teammate_down":e(True),"exposed":e(True)})
    assert d.kind=="avoid_open_revive"

def test_ring_rotation_over_low_ammo():
    d=coach({"zone_moving":e(True),"in_combat":e(True),
             "ammo_low":e(True),"cover_visible":e(True)})
    assert d.kind=="rotate_zone"

def test_objective_priority_only_for_cod():
    signals={"objective_visible":e(True),"objective_timer_low":e(True)}
    assert coach(signals,game="cod").kind=="play_objective"
    assert coach(signals,game="apex") is None

def test_convert_knock_requires_teammate_near():
    assert coach({"confirmed_enemy_knock":e(True)}) is None
    d=coach({"confirmed_enemy_knock":e(True),"teammate_near":e(True)})
    assert d.kind=="convert_knock"

def test_only_visible_high_ground():
    assert coach({"high_ground_access_visible":e(True)}) is None
    assert coach({"high_ground_access_visible":e(True),"enemy_visible":e(True)}).kind=="take_height"

def test_not_make_up_unknown_enemy_or_position():
    assert coach({"enemy_visible":e(True)}) is None
    assert coach({"health_low":e(True),"exposed":e(False)}) is None
    assert coach({}) is None

def test_stale_low_confidence_and_hallucinated_source_rejected():
    for bad in (e(True,age=1),e(True,confidence=.3),e(True,source="memory"),
                e(True,source="clip_label"), e(True,proof="")):
        assert coach({"health_low":bad,"exposed":e(True)}) is None

def test_replay_label_accepted_only_offline():
    signals={"health_low":e(True,source="clip_label"),"exposed":e(True,source="clip_label")}
    assert coach(signals) is None
    assert TacticalCoach(allow_replay_labels=True).choose(signals,NOW).kind=="break_sightline"

def test_false_observation_boolean_cannot_trigger():
    assert coach({"health_low":e(1),"exposed":e(True)}) is None
    assert coach({"health_low":e("yes"),"exposed":e(True)}) is None

def test_learning_cannot_override_survival_advice():
    d=coach({"health_low":e(True),"exposed":e(True),"squad_split":e(True),
             "enemy_visible":e(True)},weights={"teamplay":.4,"positioning":0})
    assert d.kind=="break_sightline"

def test_annotated_replay_scores_tp_and_silence():
    def flags(**kw):
        return {k:{"value":v,"proof":"annotated visible video"} for k,v in kw.items()}
    case={"game":"apex","frames":[
      {"ms":0,"signals":flags(enemy_visible=True)},
      {"ms":100,"signals":flags(health_low=True,exposed=True)},
      {"ms":200,"signals":flags(zone_moving=True,in_combat=True)},
      {"ms":300,"signals":flags(visible_enemy_count=2,visible_allies_count=0)},
      {"ms":400,"signals":flags(teammate_down=True)},
    ],"expected":[
      {"frame":1,"kind":"break_sightline"},
      {"frame":2,"kind":"rotate_zone"},
      {"frame":3,"kind":"retreat_outnumbered"},
    ]}
    result=evaluate(case)
    assert result["passed"]
    assert result["false_positives"]==0
    assert result["true_positives"]==3

def test_annotated_replay_rejects_stale():
    case={"game":"apex","frames":[{"ms":0,"signals":{
        "health_low":{"value":True,"age_ms":1000},
        "exposed":{"value":True}}}],"expected":[]}
    assert evaluate(case)["passed"]

def test_profile_uses_only_verified_reports_as_confirmed():
    reports=[
        {"game":"apex","category":"positioning","result":"loss","source":"owner_confirmed"},
        {"game":"apex","category":"positioning","result":"loss","source":"owner_confirmed"},
        {"game":"apex","category":"positioning","result":"win","source":"owner_confirmed"},
        {"game":"apex","category":"aim","result":"loss","source":"made_up"},
        {"game":"cod","category":"rotation","result":"loss","source":"owner_confirmed"},
    ]
    profile=build_profile("apex",reports,[])
    assert profile["primary_focus"]=="positioning"
    assert profile["confirmed_reports"]==3
    assert profile["weights"]["aim"]==0
    assert "cover" in profile["drill"]

def test_clip_reviews_secondary_and_game_specific():
    reviews=[{"game":"cod","weak_points":[{"category":"objective"}]}
             for _ in range(5)]
    cod=build_profile("cod",[],reviews)
    apex=build_profile("apex",[],reviews)
    assert cod["review_hypotheses"]==5 and cod["primary_focus"]=="objective"
    assert cod["confirmed_reports"]==0
    assert apex["primary_focus"] is None

def test_sparse_data_has_no_fake_personalization():
    p=build_profile("apex",[],[])
    assert p["primary_focus"] is None
    assert not any(p["weights"].values())
    assert set(DRILLS)==set(CATEGORIES)

def test_profile_corrupt_rows_cannot_leak_game_data():
    rows=[{},3,None,{"source":"owner_confirmed","game":"apex",
                     "category":"bogus","result":"loss"}]
    p=build_profile("apex",rows,[{"weak_points":["bad",None,{"category":"wrong"}]}])
    assert p["confirmed_reports"]==0 and p["primary_focus"] is None


def test_multibar_calibration_uses_one_combined_capture(tmp_path):
    from scripts.gamecoach_live import prepare_detectors
    health={"kind":"low_health","rect":[10,10,110,20],"rgb":[20,200,40]}
    shield={"kind":"low_shield","rect":[50,40,250,50],"rgb":[20,140,210]}
    files=[]
    for i,data in enumerate((health,shield)):
        p=tmp_path/f"bar{i}.json"
        p.write_text(json.dumps(data))
        files.append(p)
    region,detectors=prepare_detectors(files)
    assert region==(10,10,250,50)
    assert [kind for kind,_ in detectors]==["low_health","low_shield"]
    assert detectors[0][1].c.rect==(0,0,100,10)
    assert detectors[1][1].c.rect==(40,30,240,40)

def test_calibrating_one_kind_preserves_other_profiles(tmp_path):
    from core.gamecoach_service import CoachService
    svc=CoachService(root=tmp_path,platform="win32")
    assert svc.profile_file("apex","low_health")!=svc.profile_file("apex","low_shield")
    for k in ("low_health","low_shield"):
        svc.profile_file("apex",k).write_text(json.dumps({"kind":k}))
    assert len(svc.profile_files("apex"))==2
    assert not svc.profile_files("cod")

def test_legacy_health_profile_kept_when_shield_is_new(tmp_path):
    from core.gamecoach_service import CoachService
    svc=CoachService(root=tmp_path,platform="win32")
    svc.file("apex").write_text(json.dumps({"kind":"low_health"}))
    svc.profile_file("apex","low_shield").write_text(json.dumps({"kind":"low_shield"}))
    found=svc.profile_files("apex")
    assert len(found)==2
    assert {json.loads(p.read_text())["kind"] for p in found}=={"low_health","low_shield"}

def test_reject_duplicate_profile_kinds(tmp_path):
    import pytest
    from scripts.gamecoach_live import prepare_detectors
    data={"kind":"low_health","rect":[0,0,100,10],"rgb":[20,200,40]}
    ps=[]
    for i in range(2):
        p=tmp_path/f"r{i}.json"
        p.write_text(json.dumps(data))
        ps.append(p)
    with pytest.raises(ValueError):
        prepare_detectors(ps)


def test_player_report_brings_useful_1v2_coaching_without_screen_claims():
    from core.gamecoach_tactics import advice_from_player_report
    text=advice_from_player_report("apex","Jarvis, I see two enemies and I'm alone.")
    assert "Based on what you reported" in text
    assert "Outnumbered" in text and "one-on-one" in text

def test_player_report_low_health_in_open():
    from core.gamecoach_tactics import advice_from_player_report
    reply=advice_from_player_report("cod", "I'm low HP and I'm in the open")
    assert "Break line of sight" in reply

def test_player_report_ring_and_fight():
    from core.gamecoach_tactics import advice_from_player_report
    assert "rotate" in advice_from_player_report("apex", "The ring closing, I'm fighting").lower()

def test_player_report_avoids_guessing():
    from core.gamecoach_tactics import advice_from_player_report
    reply=advice_from_player_report("apex", "There's someone nearby, where are they?")
    assert "don't have enough" in reply
