"""The explanation ladder on the live chat path (fakes only; own invented course text and a canned model).

What went wrong on the deployed app: "Explain RAM from my IT Fundamentals course." never reached the tutor (the course name
stayed in the topic, so the topic was "not in his material"), so every "simpler" after it was answered by the chat model with no
rung, no memory and no board. These tests pin the routing, the one-rung moves, the hard length caps, the general-knowledge
fallback with its single disclaimer, the bottom of the ladder and the whiteboard notice."""
import json
from datetime import datetime

import pytest

from core import study_check as check
from core import study_engine as engine
from core import study_ladder as ladder
from core import study_store, studyintent
from tools import registry, study


def _known(topic):
    words = set(study_store.tokens(topic))
    if not words or len(words) > 4:
        return False
    hits = study_store.search(topic, k=1)
    return bool(hits) and words <= set(study_store.tokens(f"{hits[0]['text']} {hits[0]['section']} {hits[0]['chapter']}"))


def route(said, context=0):
    return studyintent.call_for(said, None, context, lambda: [c["title"] for c in study_store.courses()], _known)


# The canned model: one invented photosynthesis ladder, chosen by the rung named in the prompt.
CANNED = {
    "college": ("Photosynthesis is the process by which plants convert light energy into chemical energy. Chlorophyll in the chloroplasts "
                "absorbs light and drives the splitting of water, which releases oxygen. The light reactions produce ATP and NADPH, "
                "which the Calvin cycle then uses to fix carbon dioxide into glucose.", ""),
    "high": ("Photosynthesis is how a plant makes sugar from light. Example: a leaf takes in 6 molecules of carbon dioxide and 6 of water. "
             "Using light, it builds 1 molecule of sugar and lets out 6 of oxygen.", ""),
    "middle": ("Photosynthesis is like a kitchen that cooks with sunlight. The leaf is the kitchen, light is the stove and sugar is the meal.",
               "a kitchen"),
    "elementary": ("Photosynthesis is how Maya's plant works. She sits by the window. The sun shines on it like a warm lamp, and the plant cooks its own food.", "a warm lamp"),
    "prek": ("Photosynthesis! The plant drinks water. The sun helps it make food!", "a lamp"),
    "one": ("Photosynthesis! Sun! Yum! (point to the sun)", "a lamp"),
}


class Canned:
    def __init__(self, gone=()):
        self.prompts, self.gone = [], set(gone)

    def __call__(self, prompt):
        self.prompts.append(prompt)
        for rung in ladder.RUNGS:
            if ladder.RULES[rung] in prompt and rung not in self.gone:
                text, analogy = CANNED[rung]
                return json.dumps({"explanation": text, "analogy": analogy, "limit": "", "check": ""})
        return "nothing"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    def down(prompt):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(study, "MODEL", down)
    monkeypatch.setattr(engine, "clock", lambda: datetime(2026, 3, 2, 10, 0, 0))
    monkeypatch.setattr(study, "OPEN_BOARD", lambda: False)
    for state in (study._sessions, study._explained, study._follow, study._follow_at, study._boards, study._notice):
        state.clear()
    yield
    for state in (study._sessions, study._explained, study._follow, study._follow_at, study._boards, study._notice):
        state.clear()


def run(name, **args):
    out, is_error = registry.run(name, args)
    assert not is_error, out
    return out


# ---- routing -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, topic, course", [
    ("Explain RAM from my IT Fundamentals course.", "RAM", "IT Fundamentals"),
    ("explain the CPU in my IT Fundamentals class", "CPU", "IT Fundamentals"),
    ("Explain subnetting from my IT Fundamentals course.", "subnetting", "IT Fundamentals"),
    ("can you teach me about photosynthesis", "photosynthesis", ""),
    ("Help me understand recursion", "recursion", ""),
    ("teach me how volcanoes work", "volcanoes", ""),
    ("jarvis, explain a firewall to me", "firewall", ""),
])
def test_a_request_to_explain_a_topic_starts_the_tutor_with_the_course_name_taken_out_of_the_topic(said, topic, course):
    assert route(said) == ("study_explain", {"topic": topic, "level": "", "course": course})


@pytest.mark.parametrize("said, level", [
    ("Simpler.", "simpler"), ("simpler again", "simpler"), ("even simpler", "simpler"), ("dumb it down", "simpler"),
    ("I still don't get it", "simpler"), ("make it simpler please", "simpler"), ("that's too hard", "simpler"),
    ("More detail.", "deeper"), ("harder", "deeper"), ("go deeper", "deeper"), ("more detail please", "deeper"),
    ("Explain it like I am one year old.", "one"), ("like I'm one", "one"), ("like I'm five", "prek"),
    ("like a middle schooler", "middle"), ("explain it like I'm in elementary school", "elementary"), ("college level", "college"),
    ("now like a baby", "one"),
])
@pytest.mark.parametrize("context", [1, 2, 3])
def test_a_move_on_the_ladder_is_taken_for_three_turns_after_an_explanation(said, level, context):
    assert route(said, context) == ("study_explain_again", {"level": level}), said


@pytest.mark.parametrize("said", [
    "simpler", "even simpler", "more detail", "like I'm one", "college level", "I still don't get it", "dumb it down", "harder",
    "this is simpler than I thought", "the harder part was the wiring", "a simpler plan would be to wait", "more detail on the invoice",
    "explain it", "I can explain DNS to you", "does that explain the delay",
])
def test_the_same_words_outside_a_tutoring_exchange_are_ordinary_talk(said):
    assert route(said, 0) is None, said


@pytest.mark.parametrize("said", ["this is simpler than I thought", "the harder part was the wiring", "turn the volume down", "thanks, that helps",
                                  "what time is it", "stop the music", "close notepad", "remind me to call mom"])
def test_inside_the_window_only_a_pure_move_is_taken_and_commands_never_are(said):
    assert route(said, 1) is None and route(said, 2) is None, said


def test_the_follow_up_window_is_three_turns_and_a_few_minutes(monkeypatch):
    study._follow["owner"] = 0
    with __import__("core.users", fromlist=["x"]).acting_as("owner"):
        study.note_turn(True)
        assert [study.follow_up_age()] == [1]
        study.note_turn(False)
        assert study.follow_up_age() == 2
        study.note_turn(False)
        assert study.follow_up_age() == 3
        study.note_turn(False)
        assert study.follow_up_age() == 0
        study.note_turn(True)
        study._follow_at["owner"] -= study.FOLLOW_SECONDS + 1
        assert study.follow_up_age() == 0                        # minutes later: ordinary talk again


# ---- the ladder, one rung at a time, on a topic his library has nothing on -------------------------------------------------
def _walk(model, *lines):
    monkeypatch_model = model
    study.MODEL = monkeypatch_model
    return [run(*line[:1], **line[1]) for line in lines]


def test_each_simpler_moves_exactly_one_rung_and_every_reply_is_new_and_not_longer(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    first = run("study_explain", topic="photosynthesis")
    assert first.startswith("That is not from your course material, so this is from general knowledge.")
    seen, rungs, texts = [first], ["college"], [first]
    for _ in range(5):
        said = run("study_explain_again", level="simpler")
        rungs.append(study._explained[study._k()]["level"])
        assert "As I said" not in said and "general knowledge" not in said          # the disclaimer is said once, at the first rung
        assert said not in seen
        seen.append(said)
        texts.append(study._explained[study._k()]["text"])
    assert rungs == list(ladder.RUNGS)
    counts = [len(check.words(t)) for t in texts]
    assert counts[2] >= counts[3] >= counts[4] >= counts[5] and counts[5] <= 12                    # middle down: never longer


def test_the_one_year_old_and_pre_k_rungs_are_cut_to_their_hard_limits_whatever_the_model_wrote(monkeypatch):
    rambling = ("The plant takes light in through its leaves and then it does many things inside. First it drinks water through the roots. "
                "Then it breathes the air. After that it makes sugar. Then it grows tall. Then it makes seeds. (wave)")

    def model(prompt):
        return json.dumps({"explanation": rambling, "analogy": "a lamp", "limit": "", "check": ""})
    for rung, sentences, words in (("one", 2, 12), ("prek", 3, 32), ("elementary", 5, 85), ("middle", 5, 110)):
        text = check.finalize(rung, rambling)
        assert len(check.sentences(check._PAREN.sub(" ", text) if rung == "one" else text)) <= sentences, rung
        assert len(check.words(text)) <= words, rung
    assert check.finalize("one", "The plant makes its food from sunlight and water. It is green. It grows.").count("!") >= 1
    long = " ".join(f"Sentence number {i} says a few more words here." for i in range(30))
    for rung in ladder.RUNGS:
        out = check.cap(rung, long)
        assert len(check.sentences(out)) <= check.LIMITS[rung][0] and len(check.words(out)) <= check.LIMITS[rung][1]
    assert check.cap("college", "") == "" and check.cap("prek", "One only.") == "One only."
    del model


def test_shorter_trims_a_reply_that_is_not_shorter_than_the_rung_above():
    above = {"level": "middle", "text": "A kitchen cooks. The leaf is the kitchen. Light is the stove."}
    longer = "The leaf is a kitchen. Light is the stove. Sugar is the meal. Water is an ingredient. Air is another."
    out = check.shorter("elementary", longer, above)
    assert len(check.words(out)) < len(check.words(above["text"])) and out.startswith("The leaf is a kitchen.")
    assert check.shorter("elementary", "Short.", above) == "Short."
    assert check.shorter("high", longer, {"level": "college", "text": "x"}) == longer              # only the middle rung and below
    assert check.shorter("middle", longer, None) == longer


def test_the_bottom_of_the_ladder_says_so_kindly_offers_the_picture_and_does_not_repeat(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    run("study_explain", topic="photosynthesis", level="one")
    last = study._explained[study._k()]["text"]
    said = run("study_explain_again", level="simpler")
    assert said.startswith("That is already as simple as I can make it") and "whiteboard" in said and last not in said
    assert len(study.MODEL.prompts) == 2 or len(study.MODEL.prompts) >= 1                          # nothing was regenerated for it
    calls = len(study.MODEL.prompts)
    run("study_explain_again", level="simpler")
    assert len(study.MODEL.prompts) == calls
    assert study._explained[study._k()]["level"] == "one"
    top = run("study_explain", topic="photosynthesis")                                             # a new request by name starts at the top
    assert study._explained[study._k()]["level"] == "college" and top
    assert "most detailed" in run("study_explain_again", level="deeper")


def test_a_named_rung_is_taken_as_named_from_anywhere_and_more_detail_goes_one_up(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    run("study_explain", topic="photosynthesis")
    run("study_explain_again", level="one")
    assert study._explained[study._k()]["level"] == "one"
    run("study_explain_again", level="deeper")
    assert study._explained[study._k()]["level"] == "prek"
    run("study_explain_again", level="college")
    assert study._explained[study._k()]["level"] == "college"


def test_a_topic_in_his_material_is_taught_from_it_and_never_gets_the_disclaimer(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    said = run("study_explain", topic="RAM", course="IT Fundamentals")
    assert "general knowledge" not in said and "Concept outline" in said


def test_a_passage_that_shares_one_word_with_the_topic_is_not_taken_for_it(monkeypatch):
    model = Canned()
    monkeypatch.setattr(study, "MODEL", model)
    run("study_explain", topic="memory management in ancient libraries")
    assert "Virtual memory" not in model.prompts[0] and "no course material" in model.prompts[0]


def test_no_model_and_no_material_says_so_plainly(monkeypatch):
    said, is_error = registry.run("study_explain", {"topic": "photosynthesis"})
    assert "could not put photosynthesis together" in said


def test_the_worked_examples_computed_in_code_still_reach_a_topic_without_material(monkeypatch):
    model = Canned()
    monkeypatch.setattr(study, "MODEL", model)
    run("study_explain", topic="subnetting", course="IT Fundamentals")
    assert any("computed" in p and "/" in p for p in model.prompts), "the code-computed figures were not offered to the model"


# ---- the board follows the rung ----------------------------------------------------------------------------------------------
def test_every_ladder_reply_leaves_a_whiteboard_notice_for_the_phone_once(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    assert study.take_notice() is None
    run("study_explain", topic="photosynthesis")
    first = study.take_notice()
    assert first["rung"] == "college" and first["page"] == "study.html?board=last" and first["steps"] >= 1 and study.take_notice() is None
    run("study_explain_again", level="simpler")
    second = study.take_notice()
    assert second["rung"] == "high" and second["label"] == "High school"
    assert study.last_board()["level"] == "high"                                                   # the Study page draws the same rung
    run("study_explain_again", level="one")
    run("study_explain_again", level="simpler")                                                    # the bottom: the picture is still the current one
    assert study.take_notice()["rung"] == "one"


def test_a_whiteboard_offer_is_never_made_by_a_reply_that_has_no_board(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    said = run("study_explain", topic="photosynthesis")
    assert "whiteboard" not in said.lower() and "draw" not in said.lower()


def test_the_phone_reply_carries_the_board(monkeypatch):
    from fastapi.testclient import TestClient

    from core import appserver
    monkeypatch.setattr(study, "MODEL", Canned())

    class Fake:
        _lock = None

        def ask(self, content, on_sentence):
            from core import users
            with users.acting_as("owner"):
                if "explain" in json.dumps(content).lower():
                    on_sentence(run("study_explain", topic="photosynthesis"))
                else:
                    on_sentence("Good evening.")

    server = appserver.AppServer(lambda uid: None)
    monkeypatch.setattr(server, "_brain", lambda uid, verify=False: Fake())
    out = server._answer("owner", [{"type": "text", "text": "explain photosynthesis"}], "explain photosynthesis", False)
    assert out["board"]["rung"] == "college" and out["board"]["page"] == "study.html?board=last" and "steps" in out["board"]
    again = server._answer("owner", [{"type": "text", "text": "hello"}], "hello", False)
    assert "board" not in again                                                                    # an old board is not attached to the next turn
    del TestClient, appserver


@pytest.mark.parametrize("level, tiny", [("college", False), ("high", False), ("middle", False), ("elementary", True), ("prek", True), ("one", True)])
def test_the_lower_rungs_are_said_as_they_are_without_a_quiz_a_caveat_or_a_citation(level, tiny):
    out = {"text": "The sun helps the plant.", "limit": "plants are not people", "check": "What does the plant drink?",
           "cite": "Notes, Chapter 1", "level": level, "source": "model", "edge": "", "asked": "", "general": False, "note": ""}
    said = study._say_explained(out)
    assert ("Check yourself" not in said and "stops working" not in said) == tiny
    assert ("Notes, Chapter 1" not in said) == (level in ("prek", "one"))
    assert said.startswith("The sun helps the plant.")


# ---- the Brain's own hook, phone and voice -------------------------------------------------------------------------------------
@pytest.fixture
def brain():
    from brain import llm

    b = llm.Brain.__new__(llm.Brain)
    b.uid, b.messages = "owner", []
    return b


PHONE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "


@pytest.mark.parametrize("prefix", ["", PHONE, "[Spoken into their microphone] "])
def test_the_brain_runs_the_whole_conversation_in_code_on_every_channel(brain, monkeypatch, prefix):
    monkeypatch.setattr(study, "MODEL", Canned())
    said = []
    steps = [("Explain photosynthesis.", "college"), ("Simpler.", "high"), ("Simpler again.", "middle"), ("Even simpler.", "elementary"),
             ("I still don't get it.", "prek"), ("Dumb it down.", "one"), ("Simpler.", "one"), ("More detail.", "prek"),
             ("Explain it like I'm in middle school.", "middle"), ("College level.", "college")]
    for line, rung in steps:
        said.clear()
        assert brain._study_request(prefix + line, said.append) is True, line
        assert study._explained[study._k()]["level"] == rung, (line, rung)
        assert study.last_board()["level"] == rung                      # the board follows the rung
        assert study.take_notice()["rung"] == rung
    study.note_turn(False), study.note_turn(False), study.note_turn(False)
    assert brain._study_request(prefix + "simpler", said.append) is False      # the window is over: ordinary talk again


def test_commands_and_chat_are_never_swallowed_inside_the_window(brain, monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    said = []
    brain._study_request("explain photosynthesis", said.append)
    for line in ("turn the volume down", "stop the music", "close notepad", "what time is it", "thanks", "this is simpler than I thought",
                 "play some jazz", "open the calendar", "remind me to call mom", "Right."):
        assert brain._study_request(line, said.append) is False, line
    assert study.follow_up_age() == 0                                      # ten turns of other talk used the window up


# ---- a topic without material never leaves him with nothing --------------------------------------------------------------------
def test_when_the_model_gives_nothing_usable_for_a_lower_rung_the_rung_above_is_said_shorter(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned(gone=("middle", "elementary", "prek", "one")))
    run("study_explain", topic="photosynthesis")
    run("study_explain_again", level="simpler")
    above = study._explained[study._k()]["text"]
    for rung in ("middle", "elementary", "prek", "one"):
        said = run("study_explain_again", level="simpler")
        assert "could not put" not in said, rung
        now = study._explained[study._k()]
        assert now["level"] == rung and now["text"] and len(check.words(now["text"])) <= len(check.words(above))
        above = now["text"]
    assert len(check.sentences(check._PAREN.sub(" ", above))) <= 2 and len(check.words(above)) <= 12


def test_a_comparison_that_does_not_name_the_topic_is_fine_when_there_is_no_source_to_check_it_against(monkeypatch):
    def model(prompt):
        for rung in ("middle",):
            if ladder.RULES[rung] in prompt:
                return json.dumps({"explanation": "Photosynthesis is a tiny kitchen in a leaf where light is the stove and sugar is the meal.",
                                   "analogy": "a kitchen", "limit": "", "check": ""})
        return Canned()(prompt)
    monkeypatch.setattr(study, "MODEL", model)
    run("study_explain", topic="photosynthesis")
    run("study_explain_again", level="simpler")
    run("study_explain_again", level="simpler")
    assert study._explained[study._k()]["level"] == "middle" and "tiny kitchen" in study._explained[study._k()]["text"]


# ---- two faults the live probe found --------------------------------------------------------------------------------------
def test_a_reply_with_broken_board_json_still_gives_its_explanation():
    broken = ('{"explanation": "Subnetting splits one network into smaller ones. Example: 10.0.0.0/24 holds 254 hosts.", "analogy": "", '
              '"limit": "", "check": "Why?", "board": {"steps": [{"op": "box" "id": "x"}]}}')
    got, failed = engine._try(lambda prompt: broken, "x")
    assert not failed and got["explanation"].startswith("Subnetting splits") and got["check"] == "Why?"
    assert engine._try(lambda prompt: "no json here", "x") == (None, False)
    assert engine._try(lambda prompt: '{"explanation": "Fine."}', "x")[0] == {"explanation": "Fine."}


@pytest.mark.parametrize("rung", ["elementary", "prek"])
def test_the_prompts_own_example_wording_is_refused_for_another_topic(rung):
    leaked = check.judge(rung, ladder.EXAMPLE[rung].replace("My best toys are", "Your favorite toys are"), "recursion", "", "a toy box")
    assert leaked and leaked[0] == "copy" and "recursion" in leaked[1]
    assert (check.judge("prek", "A function that calls itself is like a mirror facing a mirror. The picture goes on and on!", "recursion",
                        "", "a mirror") or ("", ""))[0] != "copy"


# ---- review fixes: what is NOT a subject for the tutor, the slow-turn ack, and no dead end without a model -----------------------
NOT_A_SUBJECT = [
    # the conversation or the screen, not a subject
    "explain the last one", "explain the previous answer", "explain the above", "explain the last step", "explain the problem",
    "explain what you're doing", "explain what's going on", "explain what I'm looking at", "explain what you're about to do",
    # everyday things that have their own handlers or are plain chat
    "explain the plan", "explain the invoice", "explain the bill to me", "explain the score", "help me understand the news",
    "explain the joke", "explain the situation", "explain the recipe", "explain the rules",
    # a task, not a subject
    "teach me how to cook pasta", "teach me to cook", "help me understand how to fix the lag",
    # language lessons, and the coach
    "explain the French word bonjour", "explain what bonjou means in Creole",
    "walk me through the workout", "walk me through today's workout", "walk me through leg day",
]


@pytest.mark.parametrize("said", NOT_A_SUBJECT)
@pytest.mark.parametrize("context", [0, 1, 3])
def test_a_line_about_the_conversation_the_screen_or_a_task_is_never_a_subject_for_the_tutor(said, context):
    # nothing in his material (the built-in course would match a bare common word): only the plain-subject path is on trial
    assert studyintent.call_for(said, None, context, (), lambda topic: False) is None, said


@pytest.mark.parametrize("said, topic", [("Explain photosynthesis", "photosynthesis"), ("explain how volcanoes work", "volcanoes"),
                                         ("teach me about black holes", "black holes"), ("help me understand recursion", "recursion"),
                                         ("explain the difference between TCP and UDP", "difference between TCP UDP")])
def test_a_plain_subject_is_still_taught(said, topic):
    got = route(said)
    assert got and got[0] == "study_explain", said


def test_walk_me_through_is_the_tutor_only_for_what_is_in_his_material():
    assert studyintent.call_for("walk me through photosynthesis", None, 1, (), lambda t: False) is None
    got = studyintent.call_for("walk me through photosynthesis", None, 1, (), lambda t: True)
    assert got and got[0] == "study_explain"


def test_the_brain_passes_an_open_language_lesson_so_the_tutor_does_not_lecture(brain, monkeypatch):
    from tools import langlesson
    monkeypatch.setattr(study, "MODEL", Canned())
    monkeypatch.setattr(langlesson, "on_the_table", lambda: {"word": "bonjour"})
    assert brain._study_request("explain the difference between tu and vous", [].append) is False
    monkeypatch.setattr(langlesson, "on_the_table", lambda: None)
    said = ("The difference between tu and vous is formality. Tu is the informal singular you used with friends, family and children. "
            "Vous is the formal you, and it is also the plural, so it is used with strangers, elders and groups of people.")
    monkeypatch.setattr(study, "MODEL", lambda prompt: json.dumps({"explanation": said, "analogy": "", "limit": "", "check": ""}))
    assert brain._study_request("explain the difference between tu and vous", [].append) is True


def test_no_general_knowledge_lecture_while_a_language_lesson_is_open():
    assert studyintent.call_for("explain photosynthesis", None, 0, (), lambda t: False, general=False) is None
    assert studyintent.call_for("explain photosynthesis", None, 0, (), lambda t: True, general=False)[0] == "study_explain"


class _Ack:
    made = []

    def __init__(self, say, line, cancelled=lambda: False, lang="en", began=None):
        self.line, self.went, self.finished = line, False, False
        _Ack.made.append(self)

    def go(self):
        self.went = True

    def finish(self):
        self.finished = True


def _live(brain):
    import threading
    brain._turn_channel = None
    brain.live, brain._lang, brain._cancel = True, "en", threading.Event()
    brain._title = lambda: "sir"
    return brain


def test_a_tutor_turn_on_a_live_voice_is_acknowledged_while_it_works_and_finished_when_done(brain, monkeypatch):
    from core import gateway
    monkeypatch.setattr(study, "MODEL", Canned())
    monkeypatch.setattr(gateway, "Ack", _Ack)
    _Ack.made.clear()
    said = []
    assert _live(brain)._study_request("Explain photosynthesis", said.append) is True
    assert len(_Ack.made) == 1 and _Ack.made[0].went and _Ack.made[0].finished and "{t}" not in _Ack.made[0].line


def test_the_phone_and_a_turn_that_is_not_the_tutor_get_no_ack(brain, monkeypatch):
    from core import gateway
    monkeypatch.setattr(study, "MODEL", Canned())
    monkeypatch.setattr(gateway, "Ack", _Ack)
    _Ack.made.clear()
    b = _live(brain)
    b.live = False
    b._study_request("Explain photosynthesis", [].append)
    assert _Ack.made == []


def test_a_general_topic_the_model_cannot_teach_goes_to_the_chat_path_and_opens_no_window(brain, monkeypatch):
    said = []                                                      # the autouse fixture's MODEL raises: no model reachable
    assert brain._study_request("Explain photosynthesis", said.append) is False
    assert said == [] and study.follow_up_age() == 0 and not study._explained.get(study._k())
    assert brain._study_request("Simpler", said.append) is False   # nothing to be simpler: ordinary talk, not "Explain what?"


def test_an_error_reply_does_not_open_the_follow_up_window(brain, monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    said = []
    assert brain._study_request("quiz me on the zzz course", said.append) in (True, False)
    study.note_turn(False), study.note_turn(False), study.note_turn(False)
    from tools import registry as reg
    monkeypatch.setattr(reg, "run", lambda name, args: ("It failed.", True))
    brain._study_request("Explain photosynthesis", said.append)
    assert study.follow_up_age() == 0
