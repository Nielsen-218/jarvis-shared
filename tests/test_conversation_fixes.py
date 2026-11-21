"""Fixes from the owner's real conversation of 2026-11-01 (data/jarvis.log, 10:44-11:10): a numbered list answered with 'number one' was asked again,
'this human in my blender' was searched as a file name, 'open it' had no skill and the model invented actions, hash names were read aloud, and the tray
balloon crashed on a long message. Each test reproduces the exact phrasing from that log."""
import time
from pathlib import Path

import pytest

from core import craftintent, craftjobs, intelligence, notify
from tools import charjobs, rigging


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setenv("JARVIS_JOBS_INLINE", "1")
    monkeypatch.setenv("JARVIS_SKIP_GPU_GUARD", "1")
    charjobs._PENDING.clear()
    yield
    charjobs._PENDING.clear()


@pytest.fixture
def world(tmp_path, monkeypatch):
    names = ["Meshy_AI_Shadow_Vanguard_20261-48bf3cfce8ac.glb", "Meshy_AI_Shadow_Vanguard_20261031_texture_remesh_tri.blend",
             "Meshy_AI_Pink_Confidence_Look_-e3301ee79c34.glb"]
    import os
    now = time.time()
    for i, n in enumerate(names):
        (tmp_path / n).write_bytes(b"x")
        os.utime(tmp_path / n, (now - i * 100, now - i * 100))        # fixed order: the newest file is option 1 (ties would make this test flaky)
    rigged = []
    monkeypatch.setattr(rigging, "_allowed_roots", lambda: [tmp_path])
    monkeypatch.setattr(charjobs, "_rig", lambda path, kind, profile: rigged.append(str(path)) or f"RIGGED {Path(path).name}")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [])
    monkeypatch.setenv("JARVIS_BLENDER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("TEMP", str(tmp_path))
    return tmp_path, rigged


# ---- 1. "One number one option number one" after the list ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text,index", [("One number one option number one", 0), ("number two", 1), ("option 3", 2), ("the last one", -1), ("the second one please", 1),
                                        ("yes, number one", 0), ("rig number two", 1)])
def test_bare_choices_are_understood(text, index):
    assert charjobs.ordinal(text) == index


@pytest.mark.parametrize("text", ["rig the neon sentinel", "number one hundred", "one number two", "what are your rigging skills", "I want number one and the purple one", ""])
def test_other_sentences_are_not_choices(text):
    assert charjobs.ordinal(text) is None


def test_choosing_by_number_after_the_list_rigs_that_file(world):
    root, rigged = world
    out = charjobs.rig_my_character("meshy ai shadow vanguard", "", "blender")
    assert out.startswith("Which one do you mean?") and "Say the number" in out and rigged == []
    hit = craftintent.match("One number one option number one")                      # the owner's exact words from the log
    assert hit and hit[0] == "rig_my_character" and hit[1]["which"].endswith(".glb") and hit[1]["profile"] == "blender"
    assert charjobs.rig_my_character(**hit[1]).startswith("RIGGED Meshy_AI_Shadow_Vanguard_20261-48bf3cfce8ac")
    assert rigged == [hit[1]["which"]]
    assert craftintent.match("number one") is None                                     # consumed: a later 'number one' is just chat


def test_number_without_a_list_is_ordinary_chat(world):
    assert craftintent.match("number one") is None and craftintent.match("One number one option number one") is None


def test_stale_list_is_forgotten(world, monkeypatch):
    charjobs.rig_my_character("meshy ai shadow vanguard")
    real = time.time
    monkeypatch.setattr(charjobs.time, "time", lambda: real() + charjobs.CHOICE_TTL_S + 5)
    assert craftintent.match("number one") is None


# ---- 2. the list is spoken like a person would, not as hash names and '1.' fragments -----------------------------------------------------------

def test_listing_is_speakable(world):
    from brain.llm import _SENTENCE_END
    out = charjobs.rig_my_character("meshy ai shadow vanguard")
    assert "48bf3cfce8ac" not in out and "20261031" not in out and "_" not in out
    assert "option 1, Meshy AI Shadow Vanguard" in out
    pieces = [s for s in _SENTENCE_END.split(out) if s.strip()]
    assert all(len(p.strip()) > 8 for p in pieces), pieces                          # no lone '1.' read out as a sentence


# ---- 3. "rig this human in my blender" is the open scene, not a file called 'human' ----------------------------------------------------------

def test_this_human_in_my_blender_means_the_open_scene(world, monkeypatch):
    root, rigged = world
    scene = root / "Hero.blend"
    scene.write_bytes(b"x")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [{"title": f"Hero [{scene}]", "process": "blender.exe", "pid": 7, "foreground": False,
                                                              "minimized": False, "rect": [0, 0, 1, 1]}])
    hit = craftintent.match("I want you to rig this human in my blender.")
    assert hit[0] == "rig_my_character"
    out = charjobs.rig_my_character(**hit[1])
    assert out.startswith("Using your open Blender file Hero") and rigged == [str(scene)]


def test_this_human_in_my_blender_with_nothing_open_says_so(world):
    root, rigged = world
    out = charjobs.rig_my_character("this human in my blender")
    assert out.startswith("Nothing is open in Blender right now.") and rigged == []


# ---- 4. "open it" ---------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def finished_rig(world, monkeypatch):
    root, _ = world
    out = root / "rigged" / "Meshy_AI_Shadow_Vanguard-20261101-110300"
    out.mkdir(parents=True)
    (out / "char_rigged.glb").write_bytes(b"glb")
    (out / "char_rigged.fbx").write_bytes(b"fbx")
    craftjobs.submit("rigging Meshy_AI_Shadow_Vanguard_20261-48bf3cfce8ac",
                     lambda: f"Rigged x: humanoid. Files are in {out}: a .glb for Blender and an .fbx with Unreal mannequin bone names.", wait=True)
    launched = []

    class Proc:
        returncode = None

        def poll(self):
            return None
    monkeypatch.setattr(charjobs, "_spawn", lambda cmd: launched.append(cmd) or Proc())
    monkeypatch.setattr(charjobs.time, "sleep", lambda s: None)
    monkeypatch.setattr(rigging, "blender_exe", lambda: Path("C:/fake/blender.exe"))
    return out, launched


@pytest.mark.parametrize("text", ["Jarvis, can you open it for me?", "open it", "No, Jarvis, I want you to open the Meshi AI Shadow Vanguard that you just merged, that you just worked on. Can you open that?",
                                  "open the rigged character in Blender"])
def test_open_it_after_a_finished_rig_opens_that_result(finished_rig, text):
    out, launched = finished_rig
    hit = craftintent.match(text)
    assert hit == ("open_in_blender", {"which": ""}), hit
    said = charjobs.open_in_blender(**hit[1])
    assert said.startswith("Blender is starting with char_rigged.glb in a new window") and "not touched" in said
    cmd = launched[-1]
    assert "import_scene.gltf" in cmd[2] and repr(str(out / "char_rigged.glb")) in cmd[2]
    assert "use_empty=True" in cmd[2]                                                  # a fresh scene: the owner's open session is never touched


def test_open_a_named_blend_file_passes_it_straight_to_blender(world, monkeypatch):
    root, _ = world
    craftjobs._jobs.clear()
    launched = []

    class Proc:
        returncode = None
        poll = lambda self: None
    monkeypatch.setattr(charjobs, "_spawn", lambda cmd: launched.append(cmd) or Proc())
    monkeypatch.setattr(charjobs.time, "sleep", lambda s: None)
    monkeypatch.setattr(rigging, "blender_exe", lambda: Path("C:/fake/blender.exe"))
    hit = craftintent.match("open the shadow vanguard texture remesh in blender")
    assert hit[0] == "open_in_blender"
    assert charjobs.open_in_blender(**hit[1]).startswith("Blender is starting with")
    assert launched[-1][1].endswith("texture_remesh_tri.blend") and len(launched[-1]) == 2


def test_blender_that_dies_at_once_is_reported_not_claimed(finished_rig, monkeypatch):
    out, launched = finished_rig

    class Dead:
        returncode = 1

        def poll(self):
            return 1
    monkeypatch.setattr(charjobs, "_spawn", lambda cmd: Dead())
    assert "did not open it" in charjobs.open_in_blender("")


def test_open_it_with_nothing_finished_is_not_claimed_by_the_skill(world, monkeypatch):
    monkeypatch.setattr(charjobs.rigging, "blender_exe", lambda: "blender.exe")   # whether or not this PC has Blender
    craftjobs._jobs.clear()
    assert craftintent.match("can you open it for me?") is None
    assert "don't have a finished character" in charjobs.open_in_blender("it")


@pytest.mark.parametrize("text", ["open blender", "open the website", "open my notes", "open the browser", "open spotify", "how do I open a file in blender"])
def test_other_open_requests_are_left_alone(finished_rig, text):
    hit = craftintent.match(text)
    assert hit is None or hit[0] != "open_in_blender", (text, hit)


def test_pytest_guard_stops_tests_from_launching_a_real_blender(world, monkeypatch):
    monkeypatch.setattr(rigging, "blender_exe", lambda: Path("C:/fake/blender.exe"))
    (world[0] / "x.blend").write_bytes(b"x")
    with pytest.raises(AssertionError, match="real background work"):
        charjobs.open_in_blender(str(world[0] / "x.blend"))


# ---- 5. no invented actions ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", ["I will now open the file.", "I will open the Meshy AI Shadow Vanguard in Blender for you.",
                                      "I will now open Blender and create a new scene, then import the model into it.", "I'm now opening Blender.",
                                      "I see it on your right monitor.", "I can see the model on your screen.", "I'll go ahead and import it."])
def test_promises_and_sightings_with_nothing_behind_them_are_caught(sentence):
    assert intelligence.claims_done(sentence)


@pytest.mark.parametrize("sentence", ["The model is already rigged with an armature.", "Which one do you mean?", "I can rig characters from a file, a name or a picture.",
                                      "I do not see a character file by that name."])
def test_honest_sentences_pass(sentence):
    assert not intelligence.claims_done(sentence)


# ---- 5b. dry-run must precede EVERY front door (live: "[dry-run] open blender" launched Blender through PC control) ---------------------------

@pytest.mark.parametrize("phrase", ["[dry-run] open blender", "[dry-run] open the meshy ai shadow vanguard texture remesh in blender", "[dry-run] turn off my pc",
                                    "[dry-run] what time is it", "[dry-run] launch notepad on the desktop"])
def test_dry_run_runs_before_quick_answers_and_pc_control(monkeypatch, phrase):
    from brain.llm import Brain
    from core import pcfast, quick
    from tools import registry
    boom = lambda *a, **k: pytest.fail("a real front door ran during a dry run")
    for mod, name in ((quick, "handle"), (pcfast, "handle"), (registry, "run")):
        monkeypatch.setattr(mod, name, boom)
    monkeypatch.setattr(Brain, "_run", boom)
    said = []
    Brain("x", "low", "sys").ask(phrase, said.append)
    assert said and said[0].startswith("DRY RUN: nothing was executed"), said
    if "blender" in phrase or "notepad" in phrase:
        assert "PC-control request" in said[0]
    if "pc" in phrase and "off" in phrase:
        assert "PC power command" in said[0]


def test_open_the_rigged_character_in_blender_is_not_taken_by_pc_control(finished_rig):
    from core import pcfast
    assert pcfast.handle("open the rigged character in Blender") is None            # falls through to the skills
    assert craftintent.match("open the rigged character in Blender") == ("open_in_blender", {"which": ""})


# ---- 6. tray balloon limits -----------------------------------------------------------------------------------------------------------------------

# ---- 6. a fragment is answered with what was caught, in his words (upgrade 4, 2026-11-10) ------------------------------------------------

@pytest.mark.parametrize("fragment", ["quiet pencil", "frozen pencil", "Quiet pencil."])   # ("paper window": the gateway's _bare_pair)
def test_a_fragment_is_quoted_back_and_never_reaches_a_model(monkeypatch, fragment):
    """'quiet pencil' / 'paper window' (Whisper bits) opened Paint overnight; the guard stays, the template line goes.
    Since chat first (2026-11-12) a fragment is answered on the chat path, with no tool in reach
    (tests/test_chat_first.py); this quote-back is what remains where there is no chat path."""
    from brain import llm
    from core import pcfast, quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: pytest.fail("no model for a fragment"))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    heard = []
    b.ask(fragment, heard.append)
    words = fragment.strip(".").lower()
    assert len(heard) == 1 and heard[0].lower() == f"i'm not sure what you meant by '{words}', sir. what did you need?"
    assert "short phrase" not in heard[0]
    assert intelligence.generic_offer(heard[0]), "a fragment right after it is still a fragment, not an answer"
    b.ask("velvet lantern", heard.append)
    assert heard[-1] == "I'm not sure what you meant by 'velvet lantern', sir. What did you need?"


_AFTER_THE_TIME = [{"role": "user", "content": "What time is it right now?"},
                   {"role": "assistant", "content": "It's 3:32 PM."}]


@pytest.mark.parametrize("said, fragment", [
    ("And what day of the week is it?", False), ("And what day is it today?", False),
    ("Okay, and what day of the week is it?", False), ("Okay. And what day of the week is it?", False),
    ("Um, and what day of the week is it?", False), ("Jarvis, and what day of the week is it?", False),
    ("So and what day is it today?", False), ("Oh, and what's the weather like?", False),
    ("Well, who won the game?", False), ("Also, where is the meeting?", False), ("Then how long does it take?", False),
    ("and frozen pencil", True), ("but velvet lantern", True), ("okay, and quiet pencil", True), ("frozen pencil", True)])
def test_a_follow_up_question_that_opens_with_and_is_not_a_fragment(said, fragment):
    """Live 2026-11-11 on the phone: 'It's 3:32 PM.' then 'And what day of the week is it?' was answered "I'm not sure
    what you meant by 'And what day of the week is it'": two content words, and a question only counted from its first
    word after at most one 'so'/'okay'. Filler and joining words in front keep a question a question; in front of noise
    they keep it noise."""
    assert intelligence.low_signal_fragment(_AFTER_THE_TIME + [{"role": "user", "content": said}]) is fragment


@pytest.mark.parametrize("plain", ["is blender installed?", "is blender installed yet?", "where did i park?",
                                   "what's my wife's name?", "is my car locked?", "what day of the week is it?",
                                   "do it", "open spotify", "can you open spotify?", "what about some music?"])
@pytest.mark.parametrize("lead", ["and ", "okay, and ", "but ", "oh, ", "well, ", "jarvis, and ", "so and "])
def test_a_joining_word_in_front_never_changes_what_a_line_is(plain, lead):
    """The same review: with 'And' in front, the action check read 'And is Blender installed?' as a command (the plain
    question's open_in_blender call is refused as ungrounded) and 'And where did I park?' skipped the look-it-up-first
    route that keeps the model from inventing where he parked. A joining word decides nothing."""
    said = lead + plain
    assert intelligence.just_asking(said) == intelligence.just_asking(plain), said
    assert intelligence.about_them(said) == intelligence.about_them(plain), said


@pytest.mark.parametrize("said", ["And is Blender installed?", "Okay, and is Blender installed yet?"])
def test_an_action_the_model_picks_for_a_joined_question_is_refused_like_the_plain_one(said):
    import tools
    from tools import registry
    tools.load_all()
    history = _AFTER_THE_TIME + [{"role": "user", "content": said}]
    offered = {t["name"] for t in intelligence.select_tools(history, registry.schemas())}
    reply = {"model": "local", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "open_in_blender", "arguments": "{\"which\": \"\"}"}}]}}]}
    assert "open_in_blender" in offered
    with pytest.raises(intelligence.LocalFailure, match="ungrounded_tool_call"):
        intelligence.parse_reply(reply, offered, said=said, heard=intelligence.heard_in(history))


def test_long_tray_notifications_are_trimmed_not_crashed(monkeypatch):
    seen = []

    class Tray:
        def notify(self, message, title):
            if len(message) > 256 or len(title) > 64:
                raise ValueError("string too long")
            seen.append((len(message), len(title)))
    monkeypatch.setattr(notify, "tray_icon", Tray())
    notify.notify("T" * 200, "x" * 400)
    assert seen and seen[0][0] <= 250 and seen[0][1] <= 60
