"""Unknown phrases, misheard speech and questions in another language must never run a mode-changing skill
(speech-recognition language lock, interpreter mode, phone pairing); the requests people actually make still do.

Live 2026-10-30, on the deployed 3B model: "Please make corrections too." -> set_conversation_language('Haitian
Creole'), a two-word phrase from the phone app -> set_conversation_language('Kreyòl ayisyen') and pair_phone('frozen
pencil'), "we have no request to stop." -> start_interpreter('English', 'Haitian Creol'), and on the laptop
pair_phone('mesye') (the owner's Kreyol title, read off the background notes). The phrases here are generated or
varied, so the guards are checked as a mechanism rather than a list of the live lines."""
import json
import random

import pytest

import tools  # noqa: F401  (registers every skill)
from core import intelligence, users
from core.intelligence import LocalFailure, parse_reply
from tools import languages, registry

NOTE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
WORDS_A = ["frozen", "amber", "velvet", "copper", "quiet", "hollow", "paper", "crimson", "marble", "golden"]
WORDS_B = ["pencil", "lantern", "meadow", "anchor", "falcon", "ledger", "window", "garden", "button", "mirror"]
LANGUAGE_CALLS = [
    ("set_conversation_language", {"language": "Haitian Creole"}),
    ("set_conversation_language", {"language": "Kreyòl ayisyen"}),
    ("set_conversation_language", {"language": "English"}),
    ("start_interpreter", {"language_a": "English", "language_b": "Haitian Creole"}),
]


@pytest.fixture
def rng():
    seed = random.randrange(1 << 30)
    print(f"seed={seed}")
    return random.Random(seed)


def _phrase(rng):
    return f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)}"


def _reply(name, args):
    return {"model": "local", "choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def dropped(name, args, said, heard=None):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(name, args), {name}, said=said, heard=said if heard is None else heard)


def kept(name, args, said, heard=None):
    out = parse_reply(_reply(name, args), {name}, said=said, heard=said if heard is None else heard)
    assert out.stop_reason == "tool_use" and out.content[0].name == name, said
    return out.content[0].input


# -- what must never run ----------------------------------------------------------------------------------------
def test_a_bare_unknown_phrase_pairs_nobody_and_switches_no_language(rng):
    for _ in range(6):
        said = _phrase(rng)
        dropped("pair_phone", {"person": said}, said)                 # the phrase itself offered as the person
        dropped("pair_phone", {"person": "me"}, said)                 # nor the owner: nobody asked for a pairing
        for name, args in LANGUAGE_CALLS:
            dropped(name, args, said)


@pytest.mark.parametrize("said", [
    "Please make corrections too.", "we have no request to stop.", "Juris.", "Opa, lé kıyoluk unyan, hey Jarvis.",
    "HoloPencil.", "Stop.", "Number two is... I've updated the conversation language to Haitian Creole. Please "
    "proceed with your request.",                                   # Jarvis's own words, heard back through the mic
    "Sir, I apologise for the mistake. I will try again.",
])
def test_misheard_fragments_and_echoes_change_no_mode(said):
    for name, args in LANGUAGE_CALLS:
        dropped(name, args, said)
    dropped("pair_phone", {"person": "me"}, said)
    dropped("pair_phone", {"person": "mesye"}, said)


@pytest.mark.parametrize("said", [
    "Ou pale kreyòl?", "Parlez-vous français?", "Do you understand Kreyol?", "What languages can you speak?",
    "Can I speak Spanish with you?", "Èske ou konprann kreyòl?", "Habla español?",
])
def test_a_question_about_a_language_does_not_lock_it(said):
    for name, args in LANGUAGE_CALLS + [("set_conversation_language", {"language": "Spanish"}),
                                        ("set_conversation_language", {"language": "French"})]:
        dropped(name, args, said)


def test_the_language_must_be_the_one_they_named():
    dropped("set_conversation_language", {"language": "Haitian Creole"}, "Speak French with me from now on.")
    kept("set_conversation_language", {"language": "French"}, "Speak French with me from now on.")
    dropped("start_interpreter", {"language_a": "English", "language_b": "Spanish"},
            "Translate for my grandmother, she only speaks Kreyol.")
    dropped("start_interpreter", {"language_a": "English", "language_b": "Haitian Creole"}, "Translate this for me.")


def test_a_person_the_model_invented_is_never_paired(rng):
    """A title from the background notes ('mesye'), a stray phrase, or a name nobody said."""
    for person in ("mesye", "monsieur", _phrase(rng), "Pierre"):
        dropped("pair_phone", {"person": person}, "pair my phone, please")
        dropped("pair_phone", {"person": person}, "Jarvis, give me a pairing code for the app")


# -- what must still work ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, language", [
    ("From now on only speak Kreyol with me.", "Haitian Creole"),
    ("je vais parler français maintenant", "French"),
    ("Switch back to English, please.", "English"),
    ("I'm going to speak Spanish now, okay?", "Spanish"),
    ("m ap pale kreyòl ayisyen avè w", "Kreyòl ayisyen"),
    ("You keep mishearing me, lock the language to English.", "English"),
    ("Well, right now we're going to use English since you can't speak Kreyol.", "English"),
])
def test_a_language_switch_they_asked_for_is_kept(said, language):
    assert kept("set_conversation_language", {"language": language}, said)["language"] == language
    assert languages.code_for(language), language                    # ...and the skill itself knows that name


def test_automatic_detection_is_asked_for_in_words():
    kept("set_conversation_language", {"language": "auto"}, "Go back to automatic language detection.")
    kept("set_conversation_language", {"language": "auto"}, "Stop locking the language, detect it yourself.")
    dropped("set_conversation_language", {"language": "auto"}, "Please make corrections too.")


def test_interpreter_mode_starts_when_both_sides_are_clear():
    kept("start_interpreter", {"language_a": "English", "language_b": "Haitian Creole"},
         "Translate for my grandmother, she only speaks Kreyol.")
    kept("start_interpreter", {"language_a": "English", "language_b": "Spanish"},
         "Interpret between English and Spanish for us.")


def test_pairing_their_own_or_a_named_household_members_phone_is_kept():
    kept("pair_phone", {"person": "me"}, "Pair my phone.")
    kept("pair_phone", {"person": "me"}, "Jarvis, give me a pairing code for the app.")
    kept("pair_phone", {"person": "me"}, "I want to link my phone to you.")
    users.add("Maya", {})
    kept("pair_phone", {"person": "Maya"}, "Link Maya's phone too.")
    kept("pair_phone", {"person": "Maya"}, "Pair my wife's phone.")      # a known member, referred to by relation
    kept("pair_phone", {"person": "Maya"}, "Can you pair her phone?", heard="My wife Maya is here.\nCan you pair her phone?")
    dropped("pair_phone", {"person": "Maya"}, "Maya is my wife.")     # a fact, not a pairing request
    dropped("pair_phone", {"person": "Maya"}, "Pair the phone.", heard="Pair the phone.")   # Maya never named


def test_other_skills_keep_their_own_rules():
    kept("open_app", {"app": "spotify"}, "open spotify")
    dropped("open_app", {"app": "frozen pencil"}, "frozen pencil")
    assert intelligence._unheard_target("open_app", {"app": "x"}, "frozen pencil", "frozen pencil") is None


def _minimal_args(name):
    """Arguments the registry accepts for a skill, with placeholder values (what matters here is the sentence)."""
    schema = next(s["input_schema"] for s in registry.schemas() if s["name"] == name)
    fill = {"string": "x", "integer": 5, "number": 5, "boolean": True, "array": [], "object": {}}
    return {k: (p["enum"][0] if "enum" in p else fill.get(p.get("type", "string"), "x"))
            for k, p in schema.get("properties", {}).items() if k in schema.get("required", [])}


def test_an_action_with_no_grounding_rule_of_its_own_must_still_be_asked_for(rng):
    """Real-model harness, 2026-10-30: the bare phrase 'copper window' opened the camera's live view (show_camera_view
    offered for the 'window' in its description). Every action skill without a grounding rule is held to a request
    shape: an order or request word, its own name, or an answer to Jarvis's question."""
    actions = [s["name"] for s in registry.schemas()
               if intelligence._ACTS.match(s["name"]) and s["name"] not in intelligence._GROUNDING]
    assert "show_camera_view" in actions and len(actions) > 40
    for name in rng.sample(actions, 12) + ["show_camera_view"]:
        said = _phrase(rng)
        args = _minimal_args(name)
        if name in intelligence._LANGUAGE_ARGS or name in intelligence._PERSON_ARGS:
            continue
        assert not intelligence._justified(name, args, said, said), (name, said)
        about_it = f"Shall I {name.replace('_', ' ')} for you, sir?"           # answering Jarvis's question about it
        own = {intelligence._stem(x) for x in name.split("_")} - intelligence._GENERIC_PARTS
        if any(len(p) >= 3 for p in own):                                     # (a skill with a name of its own)
            assert intelligence._justified(name, args, said, None, asked=about_it), (name, said)   # (heard=None: the
            # placeholder arguments are not what's judged here; _invented has its own tests)
        assert not intelligence._justified(name, args, said, None, asked="Anything else, sir?"), (name, said)
        assert not intelligence._justified(name, args, said, None, asked="What would you like to know?"), (name, said)
        # an order with no link to the skill is not enough ("Please make corrections too." ran build_step)...
        assert not intelligence._justified(name, args, "Please make corrections too.", None), name
        # ...unless the skill is the one in use in this exchange ("turn it up" right after the volume was set)
        assert intelligence._justified(name, args, "Please do that again.", None, recent={name}), name
    # ...and the question has to name the skill itself: an everyday hint word in it ("quiet" is a volume word) is not
    # the skill. Real-model harness: "...you asked about a quiet pencil. Can you give me more context?" - "Juris." ran the volume.
    assert not intelligence._justified("volume", _minimal_args("volume"), "Juris.", None,
                                       asked="You asked about a quiet pencil. Can you give me a little more context, sir?")
    assert intelligence._justified("volume", _minimal_args("volume"), "yes", None, asked="Shall I turn the volume down, sir?")
    dropped("show_camera_view", _minimal_args("show_camera_view"), "copper window")
    kept("show_camera_view", _minimal_args("show_camera_view"), "Show me what you see.")
    kept("show_camera_view", _minimal_args("show_camera_view"), "Can I see the camera view?")
    kept("volume", _minimal_args("volume"), "louder")                                     # an everyday word for the skill
    kept("volume", _minimal_args("volume"), "A bit quieter, please.")
    kept("volume", _minimal_args("volume"), "Mute.")
    kept("volume", _minimal_args("volume"), "Make it quieter.")
    kept("companion", _minimal_args("companion"), "Companion, do a trick.")      # ("Do a trick for me." reads as a
    # question to just_asking - a pre-existing rule, not this one)
    for said in ("quiet button", "quiet meadow", "loud falcon", "the quiet garden", "music lantern", "a mute swan"):
        dropped("volume", _minimal_args("volume"), said)                                  # live: 'quiet button' ran the volume
        dropped("media_control", _minimal_args("media_control"), said)
        dropped("companion", _minimal_args("companion"), said.replace("quiet", "trick"))
    kept("wake_pc", _minimal_args("wake_pc"), "wake the pc")                              # a two-letter name word
    dropped("volume", _minimal_args("volume"), "turn it up")                              # nothing says what "it" is...
    out = parse_reply(_reply("volume", _minimal_args("volume")), {"volume"}, said="turn it up", recent={"volume"})
    assert out.stop_reason == "tool_use"                                                  # ...unless the volume was just in use
    history = [{"role": "user", "content": "louder"},
               {"role": "assistant", "content": [{"type": "tool_use", "name": "volume", "input": {}, "id": "t1"}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Volume 60%."}]},
               {"role": "assistant", "content": "Volume is at sixty percent, sir."}, {"role": "user", "content": "turn it up"}]
    assert intelligence.used_in(history) == {"volume"} and intelligence.used_in([]) == set()
    kept("set_timer", _minimal_args("set_timer"), "timer, ten minutes")                    # the skill's own name
    kept("set_timer", _minimal_args("set_timer"), "Set a timer for ten minutes.")
    # A picture of their screen or room is a capture, not a lookup: a bare phrase with a screen word in it ('crimson
    # window' read the screen) takes none; a question or an order about the screen still does.
    for name in ("read_screen", "look_at_screen", "see_pc_screen", "look_through_camera"):
        dropped(name, _minimal_args(name), "crimson window")
        dropped(name, _minimal_args(name), "the blue button")
    kept("read_screen", _minimal_args("read_screen"), "What does the error on my screen say?")
    kept("read_screen", _minimal_args("read_screen"), "what does this say?")
    kept("look_at_screen", _minimal_args("look_at_screen"), "Take a look at my screen.")
    kept("look_through_camera", _minimal_args("look_through_camera"), "What's behind me?")
    kept("read_clipboard", _minimal_args("read_clipboard"), "paste what I copied")
    question = [{"role": "assistant", "content": "Which stores shall I assign, sir?"}, {"role": "user", "content": "Costco"}]
    assert intelligence.jarvis_asked(question) == "Which stores shall I assign, sir?"
    assert not intelligence.jarvis_asked([{"role": "assistant", "content": "Done."}, {"role": "user", "content": "Costco"}])
    assert not intelligence.jarvis_asked([])
    dropped("assign_stores", _minimal_args("assign_stores"), "Costco")                      # a bare name asks for nothing...
    assert intelligence._justified("assign_stores", _minimal_args("assign_stores"), "Costco and Aldi", "Costco and Aldi",
                                   asked=intelligence.jarvis_asked(question))              # ...unless it answers Jarvis's question about it
    assert not intelligence._justified("companion", _minimal_args("companion"), "quiet falcon", "quiet falcon",
                                       asked="What would you like to know about the velvet window?")


# -- the routing that offered those skills in the first place -----------------------------------------------------
def test_the_phone_apps_channel_note_offers_no_skills_of_its_own(rng):
    """Every /api/chat message carries "[Sent from their Jarvis phone app...]": its words pulled pair_phone,
    read_screen and open_app into the model's choices for any phrase at all."""
    schemas = registry.schemas()
    for _ in range(5):
        said = _phrase(rng)
        phone = {t["name"] for t in intelligence.select_tools([{"role": "user", "content": NOTE + said}], schemas)}
        voice = {t["name"] for t in intelligence.select_tools([{"role": "user", "content": said}], schemas)}
        assert phone == voice, said                                  # the note adds nothing to the lexical routing
        assert "pair_phone" not in phone, said
    offered = {t["name"] for t in intelligence.select_tools([{"role": "user", "content": NOTE + "pair my phone"}], schemas)}
    assert "pair_phone" in offered
    blocks = [{"role": "user", "content": [{"type": "text", "text": NOTE + "frozen pencil"}]}]
    assert "pair_phone" not in {t["name"] for t in intelligence.select_tools(blocks, schemas)}


def test_language_names_are_recognised_in_any_of_their_forms():
    assert languages.named_in("only speak Kreyol with me") == {"ht"}
    assert languages.named_in("Kreyòl ayisyen, s'il vous plaît, pas le français") == {"ht", "fr"}
    assert languages.named_in("Please make corrections too.") == set()
    assert languages.code_for("Kreyòl ayisyen") == "ht" and languages.code_for("français") == "fr"
    assert "Kreyòl" not in str(languages.named_in("the english way"))
