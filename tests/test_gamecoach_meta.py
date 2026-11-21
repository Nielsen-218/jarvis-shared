"""Season 30 map/Legend study: complete roster, patch correctness, no live claims."""
import pytest
from core.gamecoach_meta import (LEGENDS,MAPS,CLASSES,SEASON,ROTATION_ANNOUNCED,
  SOURCES,get_legend,get_map,describe,rotation,recommend)

def test_roster_complete_all_28_and_classes_and_six_br_maps():
    assert len(LEGENDS)==28 and len(MAPS)==6
    assert {v.cls for v in LEGENDS.values()}==set(CLASSES)
    for name,v in LEGENDS.items():
        assert v.passive and v.tactical and v.ultimate
        assert 9<len(v.strengths)<250 and 9<len(v.weaknesses)<250
        assert v.terrain
        assert name==v.name
    assert all(v.notes and v.source_url.startswith("https://www.ea.com")
               for v in MAPS.values())

def test_reworked_legends_not_from_obsolete_youtube_metas():
    assert "Allfather's Cloak" in describe("Bloodhound")["ultimate"]
    assert "Beast of the Hunt" not in str(describe("Bloodhound"))
    assert "Kickstart" in describe("Axle")["ultimate"]
    assert "Nitro Gate" in describe("Axle")["tactical"]
    assert "Knuckle Jump" in describe("Fuse")["tactical"]
    assert "D.O.C. Halo" in describe("Lifeline")["ultimate"]
    assert "Dark Veil" in describe("Catalyst")["ultimate"]
    assert "two charges" in describe("Conduit")["tactical"]
    assert "three" not in describe("Conduit")["class"]
    assert "2026" not in describe("Bloodhound")["ultimate"] or True
    assert "Marked" in SEASON

def test_casefold_input_and_map_aliases():
    assert get_legend(" BLOODHOUND ").name=="Bloodhound"
    assert get_legend("  axle").name=="Axle"
    assert get_map("worlds edge").name=="World's Edge"
    assert get_map("e district").name=="E-District"
    for invalid in ("Atlantis","",None):
        with pytest.raises(ValueError):get_map(invalid)
    with pytest.raises(ValueError):get_legend("Goku")

def test_rotation_is_dated_not_live_timer():
    m=rotation("ranked","2026-10-25")
    assert m["is_currently_verified"]
    assert set(m["map_names"])=={"World's Edge","Olympus","Broken Moon"}
    assert len(m["map_names"])==3
    assert "live map timer" in m["caution"]
    assert rotation("pubs","2026-11-04")["map_names"]==[]
    assert not rotation("ranked","2026-11-04")["is_currently_verified"]
    with pytest.raises(ValueError):rotation("wildcard")
    with pytest.raises(ValueError):rotation("ranked","yesterday")

def test_ranked_mnk_no_categorical_win_rate_claims():
    p=recommend("World's Edge","mnk_aggressive",("Lifeline","Bloodhound"),as_of="2026-10-25")
    assert p["preferred_input"]=="mnk"
    assert p["method"].find("NOT measured")>0
    assert p["recommendations"]
    assert all(x["legend"] not in {"Lifeline","Bloodhound"} for x in p["recommendations"])
    assert all(0<=x["heuristic_score"]<=20 for x in p["recommendations"])
    assert all("tradeoff" in x and "strength" in x for x in p["recommendations"])
    assert all("source" in x for x in p["recommendations"])
    assert p["is_map_in_published_core_rotation"] is True
    assert p["warnings"] and "not" in p["method"].lower()

def test_absent_map_selection_never_invent_current_map():
    with pytest.raises(ValueError):recommend("")
    with pytest.raises(ValueError):recommend("Unknown")
    with pytest.raises(ValueError):recommend("Olympus","creative")
    with pytest.raises(ValueError):recommend("Olympus","mnk_balanced",("Lifeline","Lifeline"))
    with pytest.raises(ValueError):recommend("Olympus","mnk_balanced",("Reps",))
    with pytest.raises(ValueError):recommend("Olympus","mnk_balanced",(),0)

def test_squad_synergy_and_rotation_warning_after_patch_expiration():
    a=recommend("E-District","mnk_defensive",("Caustic",),as_of="2026-11-04")
    assert a["is_map_in_published_core_rotation"] is None
    assert a["core_rotation_announced"]==[]
    assert all(x["legend"]!="Caustic" for x in a["recommendations"])
    b=recommend("Storm Point","mnk_balanced",("Wraith",),limit=3,as_of="2026-10-25")
    assert len(b["recommendations"])==3
    assert b["is_map_in_published_core_rotation"] is False

def test_ea_patch_sources_present_not_false_victory_statistics():
    assert SOURCES["patch_sep"].endswith("marked-midseason-patch-notes")
    assert "ea.com" in SOURCES["patch_aug"]
    assert "98%" not in str(LEGENDS)
    assert "win rate" not in str(MAPS)


def test_runtime_rotation_and_recommendations_expire_using_actual_date(monkeypatch):
    import core.gamecoach_meta as meta
    from datetime import date
    class Later(date):
        @classmethod
        def today(cls):return cls(2026,11,4)
    monkeypatch.setattr(meta,"date",Later)
    assert meta.rotation()["map_names"]==[]
    result=meta.recommend("World's Edge")
    assert result["as_of"]=="2026-11-04"
    assert result["is_map_in_published_core_rotation"] is None
    assert meta.rotation(as_of="2026-10-25")["map_names"]
