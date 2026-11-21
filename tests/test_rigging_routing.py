"""The owner asked 'What are your rigging skills?' and the 3B model answered with generic Blender advice
(live, 2026-10-31). Rigging/PC-awareness questions must reach their own skills, and their answers are spoken
text, never JSON a small model can garble."""
import pytest

import tools
from core import intelligence, pchybrid, pcactions, webintent
from tools import registry, rigging

tools.load_all()


def names(text):
    return [t["name"] for t in intelligence.select_tools([{"role": "user", "content": text}], registry.schemas())]


@pytest.mark.parametrize("said", ["What are your rigging skills?", "how good are you at rigging", "Can you rig a character?"])
def test_skill_questions_route_to_the_status_skill(said):
    assert names(said)[0] in ("rigging_skills", "image_to_character", "rig_character")
    assert "rigging_skills" in names(said)
    assert "blender_reference" not in names(said)


def test_lessons_question_routes_to_lessons():
    assert names("What have you learned about rigging from your tests?")[0] == "rigging_lessons"


@pytest.mark.parametrize("said", ["Jarvis, use this image to create a fully rigged game character.", "rig this model for unreal"])
def test_rig_jobs_offer_the_job_skills_only(said):
    got = names(said)
    assert got == ["rig_my_character"]      # one tool: nothing else to pick wrongly


@pytest.mark.parametrize("said", ["what windows are open", "What windows are open on my PC right now?", "show my open windows"])
def test_window_questions_offer_free_pc_awareness(said):
    assert names(said) == ["list_windows", "observe_pc"]


def test_own_skill_questions_are_not_saved_research_recall_or_search():
    for said in ("What have you learned about rigging from your tests?", "What are your rigging skills?"):
        assert webintent.kind(said) is None
    assert webintent.kind("What did you find about Blender rigging tutorials?") == "recall"     # still works
    assert webintent.kind("Google this: blender rigging tutorial") == "search"


def test_window_question_is_a_typed_read_only_pc_action():
    plan = pchybrid.simple_plan("What windows are open on my PC right now?")
    assert plan == {"steps": [{"tool": "list_windows", "arguments": {}}]}
    assert pcactions.SCHEMAS["list_windows"] == ({}, False)          # read-only: never needs approval


def test_skill_answers_are_plain_sentences(monkeypatch):
    store = {}
    monkeypatch.setattr(rigging.storage, "load_global", lambda n, d: store.get(n, d))
    monkeypatch.setattr(rigging.storage, "save_global", lambda n, v: store.__setitem__(n, v))
    assert "not rigged anything" in rigging.rigging_skills() and not rigging.rigging_skills().startswith("{")
    assert registry.is_direct("rigging_skills") and registry.is_direct("rigging_lessons")
    rigging.record_run({"pipeline": {"kind_used": "humanoid", "bones": 20, "skin": {"final_method": "armature_auto_direct"}},
                        "stages": {"skin_valid": True, "pose_tests": "14/14 passed", "ik_fk": "4/4 passed", "export": "PASS",
                                   "reimport_animation_roundtrip": "PASS"}, "pose_tests": [{"test": "elbow.L"}]}, "x.glb", ".", True)
    text = rigging.rigging_skills()
    assert "verified" in text and "skinning" in text and "mastered" in text and "Still untested" in text

@pytest.mark.parametrize("said", ["Rig my character", "rig the model I have open in Blender", "Jarvis, rig the vanguard",
                                  "rig this for me", "make my character game ready", "use this picture to make a rigged character"])
def test_rig_requests_reach_the_resolver_first(said):
    assert names(said) == ["rig_my_character"]


@pytest.mark.parametrize("said", ["find my vanguard character", "which characters do I have saved", "what model do I have open"])
def test_find_requests_reach_find_character(said):
    assert names(said)[0] == "find_character"


def test_rig_tools_speak_instead_of_returning_json():
    for n in ("rig_character", "image_to_character", "rig_my_character", "find_character", "rigging_skills", "rigging_lessons"):
        assert registry.is_direct(n), n
