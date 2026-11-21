"""Describe -> concept: pose wording follows the creature, and the vision verdict picks the best usable drawing."""
from PIL import Image

from core import craftintent as C
from tools import makechar as M


def test_pose_prompt_follows_the_creature():
    assert "T-pose" in M.pose_prompt("a female knight in blue armor")
    assert "wings fully spread" in M.pose_prompt("a red dragon")
    assert "from the side" in M.pose_prompt("a grey wolf")


def test_best_usable_concept_wins_and_cannot_be_a_bad_pose(tmp_path):
    scores = {11: 3, 23: 8, 37: 7}
    seen = []
    def gen(prompt, seed):
        seen.append(seed)
        return Image.new("RGB", (8, 8), (seed, 0, 0))
    def vlm(images, prompt, schema):
        seed = len(seen) and None
        return {}
    verdicts = iter([scores[11], scores[23], scores[37]])
    def vlm(images, prompt, schema):
        s = next(verdicts)
        return {"full_body_visible": True, "single_character": True, "pose": "t_pose", "limbs_separated": True,
                "plain_background": True, "score": s}
    best, rows = M.concepts("a knight", tmp_path, gen=gen, vlm=vlm)
    assert seen == [11, 23, 37] and best["seed"] == 23 and not rows[0]["verdict"]["usable"]


def test_an_unusable_set_is_reported_not_hidden(tmp_path):
    gen = lambda p, seed: Image.new("RGB", (8, 8))
    vlm = lambda images, prompt, schema: {"full_body_visible": False, "single_character": True, "pose": "other",
                                          "limbs_separated": False, "plain_background": True, "score": 2}
    best, rows = M.concepts("a knight", tmp_path, gen=gen, vlm=vlm)
    assert not best["verdict"]["usable"]


def test_front_door_routes_descriptions():
    n, a = C.match("Make me a 3D dragon with blue scales")
    assert n == "make_character_from_description" and a["description"].startswith("dragon") or "dragon" in a["description"]
    assert C.match("create a character that is a tired space marine")[0] == "make_character_from_description"
    assert C.match("find my dragon model")[0] == "find_character"
