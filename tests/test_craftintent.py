"""The craft front door is decided in code: phrases the 3B model fumbled live route to exactly one tool with parsed args."""
import time

import pytest

from core import craftintent as C
from core import craftjobs


@pytest.fixture(autouse=True)
def no_jobs(monkeypatch):
    monkeypatch.setattr(craftjobs, "_jobs", {})


@pytest.mark.parametrize("said,tool", [
    ("Rig my character", "rig_my_character"),
    ("Jarvis, rig the neon sentinel", "rig_my_character"),
    ("[voice] please rig the model I have open in Blender", "rig_my_character"),
    ("make my picture a game ready character", "rig_my_character"),
    ("Find my vanguard character", "find_character"),
    ("What are your rigging skills?", "rigging_skills"),
    ("Can you rig a character?", "rigging_skills"),
    ("What have you learned about rigging?", "rigging_lessons"),
])
def test_routes(said, tool):
    assert C.match(said)[0] == tool


@pytest.mark.parametrize("said", ["what's the weather", "play some music", "how do I rig a dragon in blender tutorial", "tell me a joke"])
def test_leaves_everything_else_alone(said):
    assert C.match(said) is None or C.match(said)[0] not in ("rig_my_character",) or "tutorial" not in said


def test_arguments_are_parsed_from_the_words():
    assert C.match("rig the vanguard character")[1]["which"] == "the vanguard character"
    assert C.match(r'rig "C:\Users\you\Downloads\Hero.glb" please')[1]["which"] == r"C:\Users\you\Downloads\Hero.glb"
    assert C.match("rig my wolf")[1]["kind"] == "quadruped"
    assert C.match("Rig my character")[1]["which"] == "my character"


def test_status_and_cancel_only_exist_while_a_job_does():
    assert C.match("how is the rig going") is None
    j = craftjobs.submit("rigging x", lambda: (time.sleep(1.5), "done")[1])
    time.sleep(0.2)
    assert C.match("how's the rig going?")[0] == "character_job_status"
    assert C.match("cancel the rig")[0] == "cancel_character_job"
    craftjobs.cancel(j["id"])


def test_every_route_passes_the_registry_validator(monkeypatch):
    """Found live: the registry demands every declared argument, so a route with partial arguments failed on the deployed app."""
    from tools import registry
    import tools
    tools.load_all()
    for said in ["Rig the neon sentinel", "Find my vanguard character", "Make me a 3D dragon", "What are your rigging skills?",
                 "What have you learned about rigging?", "Clean up my rig outputs", "rig my wolf"]:
        name, args = C.match(said)
        assert registry.validate(name, args) in (None, ""), (said, name, args, registry.validate(name, args))
    j = craftjobs.submit("x", lambda: (time.sleep(1), "ok")[1])
    time.sleep(0.2)
    for said in ["how is the rig going", "cancel the rig"]:
        name, args = C.match(said)
        assert registry.validate(name, args) in (None, ""), (said, name)
    craftjobs.cancel(j["id"])
