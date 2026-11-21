"""The local model must never act on a plain question, nor invent facts about them, nor speak a typed-out call.

Llama 3's template orders a function call whenever tools are attached: it ran set_military_profile for "Where am I
stationed?" and start_project for "What should I name my new project?". Offered an answer-in-words option it instead
invented personal facts ("Where did I park?" -> a made-up spot). These pin the guards that fixed both.
"""
import json

import pytest

import tools
from core import intelligence, offline
from core.intelligence import ANSWER, LocalFailure, about_them, just_asking, parse_reply
from tools import registry

tools.load_all()
SCHEMAS = registry.schemas()


@pytest.mark.parametrize("said", [
    "What's my wife's name?", "Where am I stationed?", "What should I name my new project?", "Who won the 2018 World Cup?",
    "Explain how a heat pump works, briefly.", "Is it going to rain?", "Did you add my son's powdered milk?",
    "How do I uninstall ProtonVPN?", "What can you do?", "Hey Jarvis, what's the time", "Remind me what the code word was",
])
def test_plain_questions(said):
    assert just_asking(said)


@pytest.mark.parametrize("said", [
    "Open Spotify", "Can you open Spotify?", "Please add milk to the list", "Tell me a joke", "Remind me to call mom at 5",
    "Are you able to open Blender?", "Do you think you could turn the lights off?", "Why don't you set a timer for 5 minutes?",
    "Is it possible to restart the router?", "Could you check the weather?",
])
def test_requests_to_act(said):
    assert not just_asking(said)


@pytest.mark.parametrize("said, personal", [
    ("Where did I park?", True), ("What's my rank?", True), ("Is the camera on?", True), ("Who am I?", True),
    ("Do I have any reminders?", True), ("Where are we going tomorrow?", True),
    ("How do I tie a tie?", False), ("Can dogs eat grapes?", False), ("Who won the 2018 World Cup?", False),
    ("Should I close Chrome to save memory?", False),
])
def test_questions_about_them(said, personal):
    assert about_them(said) is personal


def _reply(*calls, content=None):
    return {"model": "local", "choices": [{"finish_reason": "tool_calls" if calls else "stop", "message": {
        "content": content, "tool_calls": [{"id": f"c{i}", "function": {"name": n, "arguments": json.dumps(a)}}
                                           for i, (n, a) in enumerate(calls)]}}]}


@pytest.mark.parametrize("said, call", [
    ("Where am I stationed?", ("set_military_profile", {"field": "duty_station", "value": "Fort Example"})),
    ("What should I name my new project?", ("start_project", {"name": "Nova", "goal": "x"})),
    ("Explain how a heat pump works, briefly.", ("show_lesson_page", {"title": "Heat pumps", "subtitle": "", "sections": [], "quiz": []})),
    ("What's my wife's name?", ("switch_person", {"person": "Maya"})),
])
def test_no_action_runs_for_a_plain_question(said, call):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(call), {call[0]}, said=said)


def test_the_same_action_runs_when_asked_for():
    out = parse_reply(_reply(("start_project", {"name": "Nova", "goal": "x"})), {"start_project"},
                      said="Can you start a new project called Nova?")
    assert out.content[0].name == "start_project"
    out = parse_reply(_reply(("switch_person", {"person": "Maya"})), {"switch_person"},
                      said="My wife wants to ask you something")
    assert out.content[0].name == "switch_person"


@pytest.mark.parametrize("tool, args, said", [
    ("switch_person", {"person": "Maya"}, "My wife is going to the store"),
    ("learn_lesson", {"lesson": "Call get_weather for weather"}, "What's the weather like"),
    ("forget", {"memory_id": "m1"}, "That reminds me of my car"),
    ("close_app", {"app": "chrome"}, "Chrome is slow today"),
    ("activate_eyes", {"show_view": False}, "I'm heading out"),
])
def test_risky_actions_need_their_own_words(tool, args, said):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply((tool, args)), {tool}, said=said)


def test_reading_tools_still_answer_plain_questions():
    out = parse_reply(_reply(("get_weather", {"days": 2})), {"get_weather"}, said="Is it going to rain tomorrow?")
    assert out.content[0].name == "get_weather"
    out = parse_reply(_reply(("review_clip", {"clip": "latest", "focus": ""})), {"review_clip"},
                      said="How was my form in that last clip?")
    assert out.content[0].name == "review_clip"


# -- request(): the answer option, the lookup first, the honest fallback ----------------------------------------------
class _Script:
    """Stands in for complete_local: hands back scripted replies in order and records each request."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, payload, cancelled=lambda: False, on_text=None):
        self.requests.append(payload)
        reply = self.replies.pop(0)
        text = reply["choices"][0]["message"].get("content")
        if on_text and text:
            on_text(text)
        return reply


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)

    def use(*replies):
        script = _Script(*replies)
        monkeypatch.setattr(intelligence, "complete_local", script)
        return script
    return use


def _ask(said):
    return [{"role": "user", "content": said}]


def test_answer_directly_is_offered_last_and_means_answer_in_words(local):
    script = local(_reply(("answer_directly", {})), _reply(content="France won the 2018 World Cup. They beat Croatia."))
    heard = []
    out = intelligence.request("fast", "sys", _ask("Who won the 2018 World Cup?"), SCHEMAS, on_text=heard.append)
    first, second = script.requests
    if first["tools"]:
        assert first["tools"][-1] == ANSWER
    assert second["tools"] == [] and out.content[0].text == "France won the 2018 World Cup. They beat Croatia."
    assert heard == ["France won the 2018 World Cup. They beat Croatia."]   # the worded answer streams


def test_a_blocked_action_becomes_a_worded_answer(local):
    local(_reply(("start_project", {"name": "Nova", "goal": "x"})), _reply(content="How about Nova or Atlas?"))
    out = intelligence.request("fast", "sys", _ask("What should I name my new project?"), SCHEMAS)
    assert [b.type for b in out.content] == ["text"]


def test_a_question_about_them_is_looked_up_not_guessed(local):
    script = local(_reply(("answer_directly", {})), _reply(("recall_memories", {"query": "park"})))
    out = intelligence.request("fast", "sys", _ask("Where did I park?"), SCHEMAS)
    lookup = {t["function"]["name"] for t in script.requests[1]["tools"]}
    assert {"recall_memories", "recall_conversations"} <= lookup               # memory search is always offered
    assert not any(intelligence._ACTS.match(n) for n in lookup)                # nothing that changes anything
    assert not lookup & intelligence._CAPTURE                                  # no camera/screen grabs
    assert ANSWER["function"]["name"] not in lookup                            # it must look
    assert out.content[0].name == "recall_memories"


def test_if_no_lookup_comes_back_it_answers_without_inventing(local):
    script = local(_reply(("answer_directly", {})), _reply(content='{"name": "bad"'), _reply(content="I don't know, sir."))
    out = intelligence.request("fast", "sys", _ask("What's my blood type?"), SCHEMAS)
    note = json.dumps(script.requests[2]["messages"][-1]["content"])
    assert "Never make it up" in note and out.content[0].text == "I don't know, sir."


def test_the_cloud_fallback_never_sees_the_answer_option(local, monkeypatch):
    local(_reply(content='{"name": "bad"'))
    seen = []
    monkeypatch.setattr(intelligence, "_cloud_fallback", lambda payload, names, cancelled: seen.append(payload) or "ok")
    assert intelligence.request("advanced", "sys", _ask("Open Spotify"), SCHEMAS) == "ok"
    assert ANSWER not in seen[0]["tools"]


# -- a typed-out call is never spoken ---------------------------------------------------------------------------------
def test_a_typed_answer_with_an_illegal_escape_is_spoken_as_its_words():
    leak = r'''{"name": "answer_directly", "parameters": {"message": "I don\'t know your checking balance."}}'''
    assert offline._unwrap(leak) == "I don't know your checking balance."
    out = parse_reply({"choices": [{"finish_reason": "stop", "message": {"content": leak}}]}, set(), said="how much?")
    assert out.content[0].text == "I don't know your checking balance."


def test_a_broken_call_shaped_text_is_never_spoken():
    with pytest.raises(LocalFailure):
        parse_reply({"choices": [{"finish_reason": "stop", "message": {
            "content": '{"name": "get_time", "parameters": {"x": broken}'}}]}, set(), said="time?")


# -- near-miss enum values --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("given, fixed", [("pause", "play_pause"), ("play", "play_pause"), ("skip", "next"),
                                          ("back", "previous"), ("NEXT", "next"), ("rewind", "rewind")])
def test_media_words_map_to_the_allowed_action(given, fixed):
    assert registry.coerce("media_control", {"action": given}) == {"action": fixed}


def test_a_repair_can_never_break_a_call(monkeypatch):
    monkeypatch.setattr(registry, "_coerce", lambda value, spec: 1 / 0)
    assert registry.coerce("get_weather", {"days": "2"}) == {"days": "2"}


# -- round 2: the owner's REAL past requests (data/jarvis.log) exposed these -----------------------------------------
@pytest.mark.parametrize("said", ["What about some music?", "How about some music?", "Can I get some music?",
                                  "Any chance of some music?", "Why not play something?"])
def test_requests_worded_as_questions_are_requests(said):
    assert not just_asking(said)


@pytest.mark.parametrize("said", ["can you tell me how to turn on pc control", "the cloud budget how do i do that",
                                  "Could you explain why my PC is slow?", "Do you know what time the store closes?"])
def test_asking_to_be_told_is_a_question(said):
    assert just_asking(said)


def test_going_to_sleep_is_jarvis_not_the_pc():
    import main
    norm = "all right we'll fix it later thank you jarvis you can go to sleep for now"
    assert main.GO_TO_SLEEP.search(norm) and not main.GO_TO_SLEEP.search("put the pc to sleep in an hour")
    for call in [("power", {"action": "sleep"}), ("pc_command", {"name": "power", "arguments": {"action": "sleep"}})]:
        with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
            parse_reply(_reply(call), {call[0]}, said="thanks jarvis, you can go to sleep for now")
    assert parse_reply(_reply(("power", {"action": "sleep"})), {"power"}, said="put the PC to sleep").content[0].name == "power"


@pytest.mark.parametrize("call, said, heard", [
    (("log_price", {"item": "powdered milk", "store": "Sam's Club", "price": 5.99, "size": "large"}),
     "the powdered milk, I'm gonna get the big one in Sam's Club", "the powdered milk, I'm gonna get the big one"),
    (("qbit_speed_limit", {"download_kbs": 1000, "upload_kbs": 1000}), "I approve the $1 limit. Go.", "I approve the $1 limit. Go."),
    (("log_food", {"food": "chicken breast", "kcal": 120, "protein_g": 30}), "Don't batter me", "Don't batter me"),
    (("move_item", {"source": "/home/user/old_folder", "destination": "/home/user/new"}), "Go ahead with both", "Go ahead with both"),
    (("youtube_transcript", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}), "YouTube.", "YouTube."),
    (("metube_download", {"url": "the link to the movie on your PC", "audio_only": False}), "send the movie to the homelab", "send the movie to the homelab"),
])
def test_made_up_amounts_links_and_contents_are_refused(call, said, heard):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(call), {call[0]}, said=said, heard=heard)


@pytest.mark.parametrize("call, said", [
    (("log_price", {"item": "milk", "store": "Aldi", "price": 3.49, "size": "gallon"}), "Milk is 3.49 a gallon at Aldi"),
    (("log_expense", {"amount": 40, "category": "gas", "note": "gas"}), "I spent 40 dollars on gas"),
    (("open_website", {"url": "https://www.youtube.com"}), "Open YouTube"),
    (("remember", {"fact": "Parked on level 3", "category": "personal", "shared": False}), "Remember that I parked on level 3"),
])
def test_values_they_gave_still_go_through(call, said):
    assert parse_reply(_reply(call), {call[0]}, said=said, heard=said).content[0].name == call[0]


@pytest.mark.parametrize("call, said", [
    (("remember", {"fact": "issue a new one", "category": "other", "shared": False}), "issue a new one."),
    (("create_lookbook", {"title": "codecs to go ahead and create a very good", "intro": "x", "items": []}),
     "codecs to go ahead and create a very good"),
    (("log_expense", {"amount": 0, "category": "food", "note": "milk"}), "We're out of milk"),
    (("look_through_camera", {"high_detail": False}), "Looking on it."),
])
def test_saving_or_looking_needs_their_intent(call, said):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(call), {call[0]}, said=said, heard=said)


def test_a_personal_fact_counts_as_wanting_it_kept():
    out = parse_reply(_reply(("remember", {"fact": "Son's name is Elijah", "category": "people", "shared": False})), {"remember"},
                      said="My son's name is Elijah.", heard="My son's name is Elijah.")
    assert out.content[0].name == "remember"


def test_the_machinery_is_never_spoken():
    text = ("Since the question doesn't require any tool calls, I'll use the answer_directly function. "
            "You're welcome, sir.")
    out = parse_reply({"choices": [{"finish_reason": "stop", "message": {"content": text}}]}, set(), said="thanks")
    assert out.content[0].text == "You're welcome, sir."
    with pytest.raises(LocalFailure):
        parse_reply({"choices": [{"finish_reason": "stop", "message": {
            "content": "I will keep my answers short, without function calls."}}]}, set(), said="ok")


def test_no_done_claims_when_nothing_ran():
    def reply(text):
        return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    out = parse_reply(reply("I've noted it. The budget is now set to $1."), set(), said="I approve the $1 limit",
                      nothing_ran=True)
    assert out.content[0].text == "I've noted it."
    with pytest.raises(LocalFailure):
        parse_reply(reply("I'll turn off the lights."), set(), said="Turn off the lights.", nothing_ran=True)
    # after a skill ran, or answering a question, the same words are fine
    assert parse_reply(reply("Your timer has been set."), set(), said="set a timer").content[0].text == "Your timer has been set."
    assert intelligence.claims_done("The law has been changed in 2020.")   # why questions are exempt (nothing_ran False)


@pytest.mark.parametrize("sentence, promise", [
    # live 2026-11-10, the one brain, with NO call in the turn
    ("I'll log that preference for the powdered milk there.", True), ("I'll pull up the price book.", True),
    ("I’ll pull up the price book, sir.", True), ("Got it, sir, I'll log that preference for the powdered milk there.", True),
    ("Let me pull up the price book.", True),
    # offers that wait for him, and promises of no skill
    ("If you like, I'll pull up the price book.", False), ("Say the word and I'll add it.", False),
    ("Shall I pull up the price book?", False), ("I'll keep that in mind.", False), ("I'll be right here, sir.", False),
    ("Let me know if you want me to add it.", False), ("Okay, I won't.", False), ("Whole milk powder is on the list.", False),
])
def test_a_promise_to_act(sentence, promise):
    assert intelligence.promises_action(sentence) is promise


@pytest.mark.parametrize("sentence", [
    # review 2026-11-10: acknowledging a fact he told Jarvis (the journal keeps what he said when the conversation ends), like
    # "I'll keep that in mind"
    "I'll remember that, sir.", "I'll note that.", "Got it, I'll make a note of that.",
    # offers whose condition comes after the promise: they wait for him
    "I'll add that to your list if you'd like.", "Sure, I'll set a reminder for that if you want me to.",
    "I'll get that sorted once you confirm.", "I'll add it to your shopping list if you want.",
    # figures of speech, no skill
    "I'll open with the good news.", "I'll turn it over to you."])
def test_not_an_empty_promise(sentence):
    assert not intelligence.promises_action(sentence)


def test_an_acknowledged_fact_is_said_never_the_failure_line():
    """'My son only drinks the Nido powdered milk.' -> "I'll remember that, sir." with no call: the whole reply was dropped and
    parse_reply raised invalid_response ('Local intelligence is unavailable...' on the local-only PC)."""
    reply = {"choices": [{"finish_reason": "stop", "message": {"content": "I'll remember that, sir."}}]}
    out = parse_reply(reply, set(), said="My son only drinks the Nido powdered milk.", idle=True)
    assert out.content[0].text == "I'll remember that, sir."
    assert intelligence.promises_action("I'll log that preference for the powdered milk there."), "still an empty promise"


def test_an_empty_promise_is_never_said_when_no_skill_ran_even_for_a_question():
    """'Did you add my son's powdered milk?' is a question (nothing_ran False), so claims_done never looked: 'I'll log that
    preference for the powdered milk there.' was said and nothing was logged (live 2026-11-10)."""
    def reply(text):
        return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    said = "Did you add my son's powdered milk?"
    out = parse_reply(reply("Whole milk powder is on the list. I'll log that preference for the powdered milk there."), set(),
                      said=said, idle=True)
    assert out.content[0].text == "Whole milk powder is on the list."
    with pytest.raises(LocalFailure):
        parse_reply(reply("I'll pull up the price book."), set(), said="How much is powdered milk at Sam's Club?", idle=True)
    # the same words after a skill ran this turn, or beside the call that keeps them, are untouched
    assert parse_reply(reply("I'll pull up the price book."), set(), said=said).content[0].text == "I'll pull up the price book."
    out = parse_reply(_reply(("price_book", {}), content="I'll pull up the price book."), {"price_book"},
                      said="How much is powdered milk at Sam's Club?", idle=True)
    assert [b.type for b in out.content] == ["text", "tool_use"] and out.content[0].text == "I'll pull up the price book."


def test_a_question_turn_with_no_call_drops_the_promise_and_keeps_the_answer(local):
    local(_reply(content="Yes, sir, whole milk powder is on the shopping list. I'll log that preference for the powdered milk there."))
    out = intelligence.request("fast", "sys", _ask("Did you add my son's powdered milk?"), SCHEMAS)
    assert out.content[0].text == "Yes, sir, whole milk powder is on the shopping list."


def test_after_a_skill_ran_a_promise_is_left_alone(local):
    history = [{"role": "user", "content": "How much is powdered milk at Sam's Club?"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "price_book", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                             "content": "powdered milk: Sam's Club $18.98 for 4.5 lb"}]}]
    local(_reply(content="Sam's Club has powdered milk at $18.98 for 4.5 pounds. I'll add it to your list next."))
    out = intelligence.request("fast", "sys", history, SCHEMAS)
    assert out.content[0].text.endswith("I'll add it to your list next.")


def test_a_tool_less_call_keeps_its_words(local):
    """Interpreter mode (Brain.translate) offers no skills: a translation is someone else's 'I'll...', said as translated."""
    local(_reply(content="I'll pull up the report tonight."))
    out = intelligence.request("fast", "You are a live spoken interpreter.", _ask("M ap rale rapò a aswè a."), [])
    assert out.content[0].text == "I'll pull up the report tonight."


def test_streamed_speech_drops_an_empty_promise_only_when_no_skill_ran():
    from brain import llm
    said = []
    live = llm._SpokenAsWritten(said.append, idle=True)
    live.feed("Whole milk powder is on the list. I'll pull up the price book. Anything else? ")
    assert said == ["Whole milk powder is on the list.", "Anything else?"]
    said.clear()
    llm._SpokenAsWritten(said.append).feed("I'll pull up the price book. ")
    assert said == ["I'll pull up the price book."]


@pytest.mark.parametrize("sentence, claim", [
    ("I'll create a good plan for the Army Fitness Test.", True), ("I'll make you a playlist.", True),
    ("I'll write that down.", True), ("I'll make sure to keep it short.", False), ("I'll keep that in mind.", False),
])
def test_promises_to_make_something(sentence, claim):
    assert intelligence.claims_done(sentence) is claim


def test_a_typed_out_answer_directly_means_answer(local):
    script = local(_reply(content="I will use the `answer_directly` function to respond. {\""),
                   _reply(content="Glad they're up, sir."))
    out = intelligence.request("fast", "sys", _ask("It's okay Jarvis, they're up."), SCHEMAS)
    assert out.content[0].text == "Glad they're up, sir." and script.requests[1]["tools"] == []


def test_a_question_about_them_with_no_skill_still_gets_looked_up(local):
    script = local(_reply(("look_through_camera", {"high_detail": False})))
    out = intelligence.request("fast", "sys", _ask("What's behind me?"), SCHEMAS)
    offered = {t["function"]["name"] for t in script.requests[0]["tools"]}
    assert {"recall_memories", "look_through_camera"} <= offered and out.content[0].name == "look_through_camera"


def test_streamed_speech_drops_the_same_sentences():
    from brain import llm
    said = []
    live = llm._SpokenAsWritten(said.append, nothing_ran=True)
    live.feed("I'll keep that in mind. I'll turn off the lights. Anything else? ")
    assert said == ["I'll keep that in mind.", "Anything else?"]


def test_a_python_style_list_is_read_as_a_list():
    args = {"garment_ids": "['blue jacket']", "avatar": "photo", "notes": ""}
    assert registry.coerce("try_on", args)["garment_ids"] == ["blue jacket"]
    code = "[__import__('os').getcwd()]"                                   # literals only, never code
    assert registry.coerce("try_on", dict(args, garment_ids=code))["garment_ids"] == code


# -- round 3: llama3.2:3b, the laptop's model when the PC is off ------------------------------------------------------
@pytest.mark.parametrize("call, said", [
    (("qbit_speed_limit", {"download_kbs": 0, "upload_kbs": 0}), "I approve the $1 limit. Go."),
    (("delete_item", {"item": "cube", "confirmed": False}), "Open blender and delete a cube"),
    (("read_screen", {"app": "Claude", "find_text": "Maya"}), "What's my wife's name?"),
    (("look_at_screen", {"all_monitors": False}), "Where did I park?"),
])
def test_torrents_the_media_drive_and_the_screen_need_their_subject(call, said):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(call), {call[0]}, said=said)


@pytest.mark.parametrize("call, said", [
    (("qbit_speed_limit", {"download_kbs": 0, "upload_kbs": 0}), "Turn off the download limits"),
    (("delete_item", {"item": "movies/old trailer.mkv", "confirmed": False}), "Delete the old trailer"),
    (("read_screen", {"app": "", "find_text": ""}), "What does this error say?"),
    (("see_pc_screen", {"all_monitors": False}), "Show my PC"),
])
def test_the_same_skills_run_when_meant(call, said):
    assert parse_reply(_reply(call), {call[0]}, said=said).content[0].name == call[0]


def test_every_guarded_skill_exists():
    assert set(intelligence._GROUNDING) <= set(registry.names())


@pytest.mark.parametrize("sentence", ["One sec, sir.", "Checking your plan.", "Pulling that up.", "One moment, sir.",
                                      "Let me check that for you.", "Getting the weather for you, sir.", "On it.",
                                      "Sure, one moment.", "Looking into it.", "Just a sec, Maya!"])
def test_openers_that_announce_a_lookup(sentence):
    assert intelligence.is_lead_in(sentence)


@pytest.mark.parametrize("sentence", ["Paris.", "One minute is sixty seconds.", "Getting the basics right helps.",
                                      "Checking your tire pressure monthly helps.", "I'm on it, but it takes a while.",
                                      "Sixteen ounces.", "One sec, sir, the oven is still hot."])
def test_sentences_that_say_something(sentence):
    assert not intelligence.is_lead_in(sentence)


def test_an_answer_doesnt_open_with_one_sec():
    out = parse_reply(_reply(content="One sec, sir. Checking your plan. Check the care label first."), set(),
                      said="How do I get a stain out of a shirt?", answering=True)
    assert out.content[0].text == "Check the care label first."
    with pytest.raises(LocalFailure):                      # only the opener: not "One sec, sir." and then silence
        parse_reply(_reply(content="One sec, sir. Checking your plan."), set(), said="Where did I park?", answering=True)
    # beside a call it's what's said while that runs; outside an answer it's left as it is
    out = parse_reply(_reply(("get_weather", {"days": 1}), content="Checking the forecast."), {"get_weather"},
                      said="Will it rain?", answering=True)
    assert out.content[0].text == "Checking the forecast."
    assert parse_reply(_reply(content="One sec, sir. Paris."), set(), said="x").content[0].text == "One sec, sir. Paris."


def test_a_made_up_part_is_left_out_and_the_rest_still_runs():
    reply = _reply(("open_app", {"app": "blender"}), ("delete_item", {"item": "cube", "confirmed": False}))
    out = parse_reply(reply, {"open_app", "delete_item"}, said="Open blender and delete a cube")
    assert [b.name for b in out.content] == ["open_app"] and out.stop_reason == "tool_use"
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):          # nothing left: refused as before
        parse_reply(_reply(("delete_item", {"item": "cube", "confirmed": False})), {"delete_item"},
                    said="Open blender and delete a cube")


def test_an_answer_that_is_only_its_opener_is_asked_for_once_more(local):
    script = local(_reply(("answer_directly", {})), _reply(content="Checking your plan, sir."),
                   _reply(content="Both of what, sir?"))
    out = intelligence.request("fast", "sys", _ask("Go ahead with both, Jarvis."), SCHEMAS)
    assert out.content[0].text == "Both of what, sir?"
    assert intelligence._SAY_IT in json.dumps(script.requests[2]["messages"][-1]["content"])
    local(_reply(("answer_directly", {})), _reply(content="One sec, sir."), _reply(content="Checking your plan."))
    with pytest.raises(LocalFailure):                                         # twice: the next tier answers
        intelligence.request("fast", "sys", _ask("Go ahead with both, Jarvis."), SCHEMAS)


def test_a_streamed_answer_skips_its_opener_and_still_lines_up(local):
    from brain import llm
    said = []
    live = llm._SpokenAsWritten(said.append)
    local(_reply(("answer_directly", {})),
          _reply(content="One sec, sir. Checking your plan. Open Settings, then Apps. Pick ProtonVPN."))
    out = intelligence.request("fast", "sys", _ask("How do I uninstall ProtonVPN?"), SCHEMAS, on_text=live.feed)
    assert out.content[0].text == "Open Settings, then Apps. Pick ProtonVPN."
    assert said == ["Open Settings, then Apps."]     # brain/llm.py says the rest from the reply, from sentence 2 on


@pytest.mark.parametrize("call, said, ok", [
    (("log_food", {"food": "batter", "kcal": 100, "protein_g": 2}), "Don't Batter Me", False),
    (("log_food", {"food": "2 eggs", "kcal": 140, "protein_g": 12}), "Log 2 eggs for breakfast", True),
    (("log_food", {"food": "burger", "kcal": 600, "protein_g": 30}), "I had a burger", True),
])
def test_logging_needs_them_telling_what_happened(call, said, ok):
    if ok:
        assert parse_reply(_reply(call), {call[0]}, said=said, heard=said).content[0].name == call[0]
    else:
        with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
            parse_reply(_reply(call), {call[0]}, said=said, heard=said)


# -- round 4: found in live local conversations (scripts/convo_local.py, llama3.2:3b) -----------------------------------
def test_a_remembered_fact_is_not_answered_with_driving_directions():
    """"Where does my sister live?" (remembered: Miami) ran directions(home -> Miami) and gave the distance."""
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(("directions", {"origin": "home", "destination": "Miami", "mode": "driving"})),
                    {"directions"}, said="Where does my sister live?")
    for said in ("How far is Costco from here?", "Directions to the airport", "How do I get to Walmart?",
                 "How long is the drive to Richmond?"):
        out = parse_reply(_reply(("directions", {"destination": "x", "mode": "driving"})), {"directions"}, said=said)
        assert out.content[0].name == "directions", said


def test_the_fact_is_then_answered_from_the_notes_without_acting(local):
    history = [{"role": "user", "content": [
        {"type": "text", "text": "<retrieved-memory>\n[m1] (personal) My sister Marie lives in Miami.\n</retrieved-memory>"},
        {"type": "text", "text": "Where does my sister live?"}]}]
    script = local(_reply(content="Marie lives in Miami, sir."))      # the notes have it: no tool round at all
    out = intelligence.request("fast", "sys", history, SCHEMAS)
    assert out.content[0].text == "Marie lives in Miami, sir." and script.requests[0]["tools"] == []
    assert intelligence._FROM_NOTES in json.dumps(script.requests[0]["messages"][-1]["content"])


@pytest.mark.parametrize("said, this_chat", [
    ("What were we just talking about?", True), ("What did you just say?", True), ("Repeat that, please.", True),
    ("What did I ask you a minute ago?", True), ("What were we talking about yesterday?", False),
    ("What did we decide last week about the car?", False), ("Where did I park?", False),
])
def test_questions_about_this_conversation(said, this_chat):
    assert intelligence.about_this_chat(said) is this_chat


def test_a_question_about_this_conversation_is_answered_from_it_not_looked_up(local):
    """It was looked up (recall_memories found nothing in the vault) and Jarvis said "we didn't have a previous
    conversation" with the conversation right in front of it."""
    history = [{"role": "user", "content": "Add eggs and milk to my shopping list."},
               {"role": "assistant", "content": "Eggs and milk are on your shopping list, sir."},
               {"role": "user", "content": "What were we just talking about?"}]
    script = local(_reply(content="Your shopping list, sir: eggs and milk."))     # no tool round at all
    out = intelligence.request("fast", "sys", history, SCHEMAS)
    assert out.content[0].text == "Your shopping list, sir: eggs and milk."
    assert script.requests[0]["tools"] == [] and intelligence._THIS_CHAT in json.dumps(script.requests[0]["messages"][-1])
    # older talk is the journal's: looked up as before
    script = local(_reply(("answer_directly", {})), _reply(("recall_conversations", {"query": "car"})))
    out = intelligence.request("fast", "sys", _ask("What did we decide yesterday about the car?"), SCHEMAS)
    assert out.content[0].name == "recall_conversations"


def test_the_answer_after_a_tool_ran_does_not_open_with_one_sec(local):
    """"One sec, sir. Eggs and milk have been added to your shopping list.": the lookup it announces already ran."""
    from brain import llm
    history = [{"role": "user", "content": "Add eggs and milk to my shopping list."},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "add_note",
                                                  "input": {"list_name": "shopping", "text": "eggs, milk"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Added to shopping (1 open item)."}]}]
    said = []
    live = llm._SpokenAsWritten(said.append)
    local(_reply(content="One sec, sir. Eggs and milk have been added to your shopping list. Anything else?"))
    out = intelligence.request("fast", "sys", history, SCHEMAS, on_text=live.feed)
    assert out.content[0].text == "Eggs and milk have been added to your shopping list. Anything else?"
    assert said == ["Eggs and milk have been added to your shopping list."]   # streamed the same way, opener and all
    # a reply that goes on to call another tool keeps its words (they're said while it runs)
    local(_reply(("add_note", {"list_name": "shopping", "text": "milk"}), content="Adding the milk separately."))
    out = intelligence.request("fast", "sys", history, SCHEMAS)
    assert out.content[0].text == "Adding the milk separately." and out.content[1].name == "add_note"


# -- round 5: the fast tier's own refusals, measured (scripts/probe_fast.py on the owner's lines) ------------------------
def test_small_talk_answered_with_only_an_opener_is_asked_again_then_answered_in_jarviss_words(local):
    """"Thank you.", "Yes.", "Okay.", "Bah!" each got back only "One sec, sir." (7 of 11 refusals) and escalated."""
    script = local(_reply(content="One sec, sir."), _reply(content="You're most welcome, sir."))
    out = intelligence.request("fast", "sys", _ask("Thank you."), SCHEMAS)
    assert out.content[0].text == "You're most welcome, sir."
    assert intelligence._SAY_IT in json.dumps(script.requests[1]["messages"][-1]["content"])
    local(_reply(content="One sec, sir."), _reply(content="One moment, sir. Checking your plan."))
    out = intelligence.request("fast", "sys", _ask("Thank you."), SCHEMAS)
    from core import character

    def plain(text):
        return text.replace(", sir", "")
    assert plain(out.content[0].text) in character.LINES["thanks"]       # a word of our own beats "unavailable"
    local(_reply(content="One sec, sir."), _reply(content="One sec, sir."))
    assert plain(intelligence.request("fast", "sys", _ask("Okay."), SCHEMAS).content[0].text) in character.LINES["ok"]
    local(_reply(content="One sec, sir."), _reply(content="One sec, sir."))
    assert plain(intelligence.request("fast", "sys", _ask("Yes."), SCHEMAS).content[0].text) in character.LINES["yes"]


def test_a_bare_yes_is_never_small_talk():
    assert not intelligence.small_talk("Yes.") and not intelligence.small_talk("Yeah.", asked=True)
    assert intelligence.canned_small_talk("Shut down the PC") is None


def test_a_call_written_out_as_text_is_made_and_judged_like_a_real_one():
    """4 of 11 refusals: {"name": "control_computer", "parameters": {"parameters": {...}}} typed as the answer."""
    typed = '{"name": "get_weather", "parameters": {"parameters": {"place": "Springfield, IL", "days": "2"}}}'
    out = parse_reply(_reply(content=typed), {"get_weather"}, said="What's the weather?")
    assert out.content[0].name == "get_weather" and out.content[0].input == {"place": "Springfield, IL", "days": 2}
    assert out.stop_reason == "tool_use"
    with pytest.raises(LocalFailure, match="invalid_tool_call"):          # a skill that wasn't offered: as before
        parse_reply(_reply(content='{"name": "power", "parameters": {"action": "shutdown"}}'), {"get_weather"}, said="x")
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):       # grounding applies to it too
        parse_reply(_reply(content='{"name": "open_app", "parameters": {"app": "haha"}}'), {"open_app"}, said="haha")
    with pytest.raises(LocalFailure, match="invalid_response"):           # a cut-off one is still never spoken
        parse_reply(_reply(content='{"name": "get_weather", "parameters": {"pla'), {"get_weather"}, said="x")


@pytest.mark.parametrize("said", ["debrief me", "status on your investigation.", "Give me a status update",
                                  "catch me up", "summarize my week", "where are we at with the homelab"])
def test_a_request_for_a_report_on_them_is_looked_up_not_invented(said, local):
    """"debrief me" got a whole invented gaming session with no tool run."""
    assert intelligence.about_them(said)
    # With a lexical shortlist the model first picks answer_directly; with none, the lookup round comes first.
    first = [_reply(("answer_directly", {}))] if intelligence.select_tools(_ask(said), SCHEMAS) else []
    script = local(*first, _reply(content='{"name": "bad"'), _reply(content="Nothing on record, sir."))
    out = intelligence.request("fast", "sys", _ask(said), SCHEMAS)
    lookup = {t["function"]["name"] for t in script.requests[len(first)]["tools"]}
    assert {"recall_memories", "recall_conversations"} <= lookup and not any(intelligence._ACTS.match(n) for n in lookup)
    assert out.content[0].text == "Nothing on record, sir."


def test_everyday_words_shortlist_the_companion_and_the_journal():
    from core.intelligence import select_tools
    names = lambda said: [t["name"] for t in select_tools([{"role": "user", "content": said}], SCHEMAS)]  # noqa: E731
    assert "companion" in names("play rock, paper, scissors.") and "companion" in names("do some tricks.")
    assert names("What did we talk about yesterday?")[0] == "recall_conversations"


def test_a_typed_call_that_echoes_the_tools_schema_keeps_only_the_values():
    """'play rock, paper, scissors' -> {"name":"companion","parameters":{"action":"play","parameters":{"type":"object",...}}}"""
    typed = ('{"name":"companion","parameters":{"action":"play","parameters":{"type":"object","required":["action"],'
             '"properties":{"action":{"type":"string","enum":["play"]}}}}}')
    out = parse_reply(_reply(content=typed), {"companion"}, said="play rock, paper, scissors.")
    assert out.content[0].name == "companion" and out.content[0].input == {"action": "play"}


def test_past_talk_is_the_journals(local):
    from core.intelligence import select_tools
    assert [t["name"] for t in select_tools(_ask("What did we talk about yesterday?"), SCHEMAS)] == ["recall_conversations"]
    script = local(_reply(("answer_directly", {})), _reply(("recall_conversations", {"query": "yesterday"})))
    out = intelligence.request("fast", "sys", _ask("What did we decide yesterday about the car?"), SCHEMAS)
    assert out.content[0].name == "recall_conversations"
    assert "recall_memories" not in {t["function"]["name"] for t in script.requests[1]["tools"]}


def test_a_structured_request_gets_its_json_back_whole(local):
    """pcplanner and the journal ask for typed JSON through request(); parse_reply took every JSON for a leaked call."""
    plan = '{"summary": "We planned dinner.", "open": [], "facts": [], "lessons": []}'
    local(_reply(content=plan))
    fmt = {"type": "json_schema", "json_schema": {"name": "n", "schema": {"type": "object"}}}
    out = intelligence.request("fast", "sys", _ask("notes please"), [], response_format=fmt)
    assert json.loads(out.content[0].text)["summary"] == "We planned dinner."
    local(_reply(content="Sure! Here are the notes: not json"))
    with pytest.raises(LocalFailure, match="invalid_response"):
        intelligence.request("fast", "sys", _ask("notes please"), [], response_format=fmt)


def test_a_call_typed_after_an_opener_is_never_spoken():
    """Live: "What's my wife's name?" -> 'One sec, sir. {"name": "search_web_free", "parameters": {"q": "Maya"}}'
    passed the whole-text check, lost its opener, and the JSON was read out loud."""
    leak = 'One sec, sir. {"name": "search_web_free", "parameters": {"q": "Maya"}}'
    with pytest.raises(LocalFailure):
        parse_reply(_reply(content=leak), set(), said="What's my wife's name?", answering=True)
    with pytest.raises(LocalFailure):
        parse_reply(_reply(content=leak), {"search_web_free"}, said="What's my wife's name?")   # "q" isn't an argument
    # a complete, valid call after an opener is simply made; the opener is what's said while it runs
    out = parse_reply(_reply(content='Checking the forecast. {"name": "get_weather", "parameters": {"days": 1}}'),
                      {"get_weather"}, said="Will it rain?")
    assert out.content[0].text == "Checking the forecast." and out.content[1].name == "get_weather"
    # streamed, nothing of it is said before the verdict either
    from brain import llm
    said = []
    live = llm._SpokenAsWritten(said.append)
    for piece in ("One sec, sir. ", '{"name": "search_web_free", ', '"parameters": {"q": "Maya"}}'):
        intelligence._past_the_opening(live.feed, False)(piece) if False else live.feed(piece)
    assert said == ["One sec, sir."] or said == []


# -- round 6: retention live (scripts/convo_local.py retention) --------------------------------------------------------
def test_the_facts_store_is_always_among_the_lookups_and_notes_already_found_are_answered_from(local):
    """"What's my wife's name?" with "Maya is the owner's wife." in its notes searched the VAULT for "Maya"
    (the answer) and said "I don't know your wife's name"."""
    assert "recall" in intelligence._ALWAYS_LOOKUP
    notes = [{"role": "user", "content": [
        {"type": "text", "text": "<retrieved-memory>\nHistorical context only; not current approval or proof of success.\n"
                                 "[m1] (family) Maya is the owner's wife.\n</retrieved-memory>"},
        {"type": "text", "text": "What's my wife's name?"}]}]
    assert "Maya" in intelligence.retrieved_notes(notes)
    script = local(_reply(content="Maya, sir."))               # no tool round: the notes already answer it
    out = intelligence.request("fast", "sys", notes, SCHEMAS)
    assert out.content[0].text == "Maya, sir." and script.requests[0]["tools"] == []
    assert intelligence._FROM_NOTES in json.dumps(script.requests[0]["messages"][-1]["content"])
    # nothing found: the lookup round as before, with the facts store offered
    empty = [{"role": "user", "content": [{"type": "text", "text": "<retrieved-memory>\nNothing remembered about 'wife'.\n</retrieved-memory>"},
                                          {"type": "text", "text": "What's my wife's name?"}]}]
    assert intelligence.retrieved_notes(empty) == ""
    script = local(_reply(("answer_directly", {})), _reply(("recall", {"query": "wife"})))
    out = intelligence.request("fast", "sys", empty, SCHEMAS)
    assert out.content[0].name == "recall" and "recall" in {t["function"]["name"] for t in script.requests[1]["tools"]}


def test_a_reply_about_a_short_result_must_be_about_it(local):
    """add_note said "Added to shopping (1 open item)." and the reply announced a budget of two hundred dollars."""
    history = [{"role": "user", "content": "Add eggs and milk to my shopping list."},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "add_note",
                                                  "input": {"list_name": "shopping", "text": "eggs, milk"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Added to shopping (1 open item)."}]}]
    local(_reply(content="You have a little over two hundred dollars left on your budget."))
    with pytest.raises(LocalFailure, match="ungrounded_answer"):
        intelligence.request("fast", "sys", history, SCHEMAS)
    heard = []
    local(_reply(content="You have two hundred dollars left on your budget."))
    with pytest.raises(LocalFailure, match="ungrounded_answer"):
        intelligence.request("fast", "sys", history, SCHEMAS, on_text=heard.append)
    assert heard == []                                                    # nothing of it was said while it streamed
    local(_reply(content="Eggs and milk are on your shopping list, sir. Anything else?"))
    out = intelligence.request("fast", "sys", history, SCHEMAS, on_text=heard.append)
    assert out.content[0].text.startswith("Eggs and milk are on your shopping list") and heard   # about it: streamed
    local(_reply(content="Done, sir, both added."))                       # about what they asked: fine
    assert intelligence.request("fast", "sys", history, SCHEMAS).content[0].text == "Done, sir, both added."
    long = [dict(history[-1], content=[{"type": "tool_result", "tool_use_id": "t1", "content": "x " * 300}])]
    local(_reply(content="Quite a list, sir. Shall I read it?"))         # a long result still streams and isn't judged
    heard.clear()
    intelligence.request("fast", "sys", history[:-1] + long, SCHEMAS, on_text=heard.append)
    assert heard


# -- round 7: a write on a statement ------------------------------------------------------------------------------------
def test_a_trip_is_planned_only_when_they_ask_for_one():
    """Live: "Probably the week before Christmas. Don't book anything yet." -> plan_trip with invented dates."""
    plan = ("plan_trip", {"name": "Miami Christmas", "destination": "Miami", "start": "2026-12-01", "end": "2026-12-15"})
    for said in ("Probably the week before Christmas. Don't book anything yet.",
                 "So my wife Maya and I are thinking of driving down to Miami in December to see my sister Marie."):
        with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
            parse_reply(_reply(plan), {"plan_trip"}, said=said)
    for said in ("Plan a trip to Miami for the week before Christmas.", "Can you set up the Miami trip?",
                 "Book it: Miami, December first to the fifteenth."):
        assert parse_reply(_reply(plan), {"plan_trip"}, said=said).content[0].name == "plan_trip", said
    # the same statement may still be looked up (driving -> directions is grounded), just not acted on
    assert parse_reply(_reply(("directions", {"destination": "Miami", "mode": "driving"})), {"directions"},
                       said="We're thinking of driving down to Miami in December.").content[0].name == "directions"


@pytest.mark.parametrize("said, negated", [
    ("Don't book anything yet.", True), ("No need to remind me, I've got it.", True),
    ("I'm not going to buy it after all.", True), ("Don't save that.", True),
    ("Don't book the hotel, just remind me next week.", False),            # an order follows
    ("We're out of milk, don't forget to add it.", False),                 # "add it" is the order
    ("Can you not add sugar to the list but add honey?", False),
    ("Book the hotel.", False), ("It's okay.", False),
])
def test_saying_not_to_do_something_is_not_a_request(said, negated):
    assert intelligence.says_not_to(said) is negated


def test_an_action_they_said_not_to_take_is_refused_but_a_following_order_runs():
    reminder = ("set_reminder", {"text": "call mom", "when": "2026-11-06T17:00", "repeat": "none", "shared": False})
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(reminder), {"set_reminder"}, said="No need to remind me about mom, I've got it.")
    out = parse_reply(_reply(reminder), {"set_reminder"}, said="Don't book the hotel, just remind me to call mom next week.")
    assert out.content[0].name == "set_reminder"


def test_a_note_that_answers_the_question_is_not_ignored(local):
    """Live, 1 run in 3: "I don't know your wife's name." with "Maya is the owner's wife." attached."""
    notes = [{"role": "user", "content": [
        {"type": "text", "text": "<retrieved-memory>\n[m1] (family) Maya is the owner's wife.\n</retrieved-memory>"},
        {"type": "text", "text": "What's my wife's name?"}]}]
    script = local(_reply(content="I don't know your wife's name, sir."), _reply(content="Maya, sir."))
    heard = []
    out = intelligence.request("fast", "sys", notes, SCHEMAS, on_text=heard.append)
    assert out.content[0].text == "Maya, sir."
    assert heard == []                 # live, the denial was streamed aloud before the retry's answer replaced it
    assert "Your notes say" in json.dumps(script.requests[1]["messages"][-1]["content"])
    local(_reply(content="I don't know."), _reply(content="I really don't know, sir."))
    with pytest.raises(LocalFailure, match="ignored_notes"):                 # the stronger tier's turn
        intelligence.request("fast", "sys", notes, SCHEMAS)
    # a note about something else: "I don't know" stands, nothing is forced out of unrelated notes
    other = [dict(notes[0], content=[notes[0]["content"][0], {"type": "text", "text": "What's my Wi-Fi password?"}])]
    first = [_reply(("answer_directly", {}))] if intelligence.select_tools(other, SCHEMAS) else []
    local(*first, _reply(content="I don't know your Wi-Fi password, sir."))
    assert intelligence.request("fast", "sys", other, SCHEMAS).content[0].text == "I don't know your Wi-Fi password, sir."


# -- round 8: after PR #137 went live ------------------------------------------------------------------------------------
def test_a_question_in_the_last_sentence_is_a_question():
    """"Okay, thanks. Remind me what we were just doing?" ran set_reminder (and with a bad time, then claimed it)."""
    said = "Okay, thanks. Remind me what we were just doing?"
    assert just_asking(said) and intelligence.about_this_chat(said)
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply(("set_reminder", {"text": "review", "when": "in 20 minutes", "repeat": "none", "shared": False})),
                    {"set_reminder"}, said=said)
    assert not just_asking("What's up. Open Spotify.")                    # the last sentence rules


def _after(result, said="What's on my shopping list?", tool="read_list", args=None):
    return [{"role": "user", "content": said},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": args or {"list_name": "shopping", "include_done": False}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": result}]}]


def test_an_answer_to_a_question_must_carry_what_the_tool_found(local):
    """read_list found "[cc8fa] (shopping) eggs, milk"; the reply: "You can view the shopping list by saying 'read
    shopping list'." (grounded on "shopping", informative about nothing)."""
    local(_reply(content="You can view the shopping list by saying 'read shopping list'."))
    with pytest.raises(LocalFailure, match="uninformative_answer"):
        intelligence.request("fast", "sys", _after("[cc8fa] (shopping) eggs, milk"), SCHEMAS)
    local(_reply(content="Your shopping list has eggs and milk on it, sir."))
    out = intelligence.request("fast", "sys", _after("[cc8fa] (shopping) eggs, milk"), SCHEMAS)
    assert out.content[0].text.startswith("Your shopping list has eggs")
    # a request (not a question) is judged by the older rule only
    local(_reply(content="Eggs added, sir."))
    out = intelligence.request("fast", "sys", _after("Added to shopping (1 open item).", said="Add eggs to my shopping list.",
                                                     tool="add_note", args={"list_name": "shopping", "text": "eggs"}), SCHEMAS)
    assert out.content[0].text == "Eggs added, sir."


def test_a_claim_of_success_after_a_skill_said_it_could_not_is_refused(local):
    """set_reminder: "Couldn't read that time; use ISO format..." -> "I've set a reminder for you to review it"."""
    history = _after("Couldn't read that time; use ISO format like 2026-10-11T09:00.", said="Remind me in 20 minutes to review it.",
                     tool="set_reminder", args={"text": "review it", "when": "in 20 minutes", "repeat": "none", "shared": False})
    local(_reply(content="I've set a reminder for you to review it in 20 minutes."))
    with pytest.raises(LocalFailure, match="claimed_despite_failure"):
        intelligence.request("fast", "sys", history, SCHEMAS)
    local(_reply(content="I couldn't read that time, sir. Shall I set it for 12:20?"))
    out = intelligence.request("fast", "sys", history, SCHEMAS)
    assert out.content[0].text.startswith("I couldn't read that time")


# -- round 9: a bare name is no fact ---------------------------------------------------------------------------------------
def test_a_fragment_fact_is_rebuilt_from_their_own_sentence():
    """Live: "Remember that my wife's name is Maya." -> remember(fact="Maya"): unrecallable, uncorrectable."""
    from core.intelligence import whole_fact
    assert whole_fact("Remember that my wife's name is Maya.") == "My wife's name is Maya."
    assert whole_fact("Actually, correction: my wife's name is Marie, not Maya.") == "My wife's name is Marie."
    assert whole_fact("Also remember my sister Marie lives in Miami and my son is Noah.") == "My sister Marie lives in Miami and my son is Noah."
    assert whole_fact("Remember that.") == "" and whole_fact("What's my wife's name?") == ""
    out = parse_reply(_reply(("remember", {"fact": "Maya", "category": "personal", "shared": False})), {"remember"},
                      said="Remember that my wife's name is Maya.", heard="Remember that my wife's name is Maya.")
    assert out.content[0].input["fact"] == "My wife's name is Maya."
    whole = ("remember", {"fact": "His sister Marie lives in Miami.", "category": "people", "shared": False})
    out = parse_reply(_reply(whole), {"remember"}, said="Remember my sister Marie lives in Miami.", heard="my sister Marie lives in Miami")
    assert out.content[0].input["fact"] == "His sister Marie lives in Miami."          # a real fact is left alone
    # live: "Maya is the owner of this PC" for "my wife's name is Maya" (one shared word let it through)
    made_up = ("remember", {"fact": "Maya is the owner of this PC", "category": "personal", "shared": False})
    out = parse_reply(_reply(made_up), {"remember"}, said="Remember that my wife's name is Maya.",
                      heard="Remember that my wife's name is Maya.")
    assert out.content[0].input["fact"] == "My wife's name is Maya."
    # end to end in memory: the rebuilt facts recall and correct
    from tools import memory
    memory.remember("My wife's name is Maya.", "personal", False)
    out = memory.remember("My wife's name is Marie.", "personal", False)
    assert "this replaces" in out and [m["fact"] for m in memory._all()] == ["My wife's name is Marie."]
    assert "Marie" in memory.recall("wife")


def test_their_own_correction_wins_over_the_models_copy_of_the_old_value():
    """Live: "Actually, correction: my wife's name is Marie, not Maya." -> remember("My wife's name is Maya.")
    -> "Already noted, sir." The user's sentence names the new value; the model's fact contradicts it."""
    said = "Actually, correction: my wife's name is Marie, not Maya."
    out = parse_reply(_reply(("remember", {"fact": "My wife's name is Maya.", "category": "people", "shared": False})),
                      {"remember"}, said=said, heard=said)
    assert out.content[0].input["fact"] == "My wife's name is Marie."
    out = parse_reply(_reply(("remember", {"fact": "My wife's name is Marie.", "category": "people", "shared": False})),
                      {"remember"}, said=said, heard=said)
    assert out.content[0].input["fact"] == "My wife's name is Marie."                  # agreeing: left alone
    out = parse_reply(_reply(("remember", {"fact": "His sister Marie lives in Miami.", "category": "people", "shared": False})),
                      {"remember"}, said="Also remember my sister Marie lives in Miami.", heard="my sister Marie lives in Miami")
    assert out.content[0].input["fact"] == "His sister Marie lives in Miami."         # no slot contradiction: left alone


def test_an_annoyed_repeat_of_his_correction_still_wins():
    """'I'm going to tell you one more time: my wife's name is Marie, not Maya.' -> remember('...is Maya.'):
    the plan guard read 'going to' / ''ll' anywhere in the sentence, so his correction lost and Jarvis said 'Already
    noted' for the old value."""
    from tools import memory
    memory.remember("My wife's name is Maya.", "people", False)
    for said in ("I'm going to tell you one more time: my wife's name is Marie, not Maya.",
                 "I'll say it again, my wife's name is Marie, not Maya."):
        out = parse_reply(_reply(("remember", {"fact": "My wife's name is Maya.", "category": "people", "shared": False})),
                          {"remember"}, said=said, heard=said)
        assert "Marie" in out.content[0].input["fact"] and "Maya" not in out.content[0].input["fact"], said
    assert "this replaces" in memory.remember(out.content[0].input["fact"], "people", False)
    assert "Maya" not in memory.recall("wife")


def test_a_plan_he_states_is_saved_as_a_plan_never_as_his_home_now():
    """'I'm going to live in Denver next month.' -> remember('I live in Denver.') (every word his) closed Springfield."""
    from tools import memory
    memory.remember("They live in Springfield.", "personal", False)
    said = "I'm going to live in Denver next month."
    out = parse_reply(_reply(("remember", {"fact": "I live in Denver.", "category": "personal", "shared": False})),
                      {"remember"}, said=said, heard=said)
    assert out.content[0].input["fact"] == "I'm going to live in Denver next month."
    memory.remember(out.content[0].input["fact"], "personal", False)
    assert "Springfield" in memory.recall("where do I live")
    said = "My address is going to be 12 Elm Street."
    out = parse_reply(_reply(("remember", {"fact": "My address is 12 Elm Street.", "category": "personal", "shared": False})),
                      {"remember"}, said=said, heard=said)
    assert out.content[0].input["fact"] == said
    said = "Remember that I live in Denver now."                                 # true now: left as the model wrote it
    out = parse_reply(_reply(("remember", {"fact": "I live in Denver now.", "category": "personal", "shared": False})),
                      {"remember"}, said=said, heard=said)
    assert out.content[0].input["fact"] == "I live in Denver now."
    # "'ll" in another statement of his is no plan: still the value now, and it corrects the old one
    memory.remember("My wife's name is Maya.", "people", False)
    said = "Remember that my wife's name is Marie, I'll tell her you said hi."
    out = parse_reply(_reply(("remember", {"fact": "My wife's name is Marie.", "category": "people", "shared": False})),
                      {"remember"}, said=said, heard=said)
    assert "this replaces" in memory.remember(out.content[0].input["fact"], "people", False)


def test_a_stated_single_valued_fact_is_remembered_even_when_the_model_saves_something_else():
    """Three live runs: for "correction: my wife's name is Marie, not Maya" the model re-saved the previous
    turn's sister fact -> "Already noted" -> the wife's name stayed Maya."""
    said = "Actually, correction: my wife's name is Marie, not Maya."
    out = parse_reply(_reply(("remember", {"fact": "His sister Marie lives in Miami.", "category": "people", "shared": False})),
                      {"remember"}, said=said, heard="my sister Marie lives in Miami " + said)
    assert out.content[0].input["fact"] == "My wife's name is Marie."
    # a non-slot statement keeps the model's (grounded) fact
    out = parse_reply(_reply(("remember", {"fact": "He likes spaghetti.", "category": "preference", "shared": False})),
                      {"remember"}, said="Remember that I like spaghetti.", heard="I like spaghetti")
    assert out.content[0].input["fact"] == "He likes spaghetti."


def test_a_failure_is_reported_in_the_skills_words_never_with_an_invented_cause(local):
    """Live: "The weather service isn't reachable right now." -> "The weather service is currently down for maintenance."."""
    history = _after("The weather service isn't reachable right now.", said="What's the weather like right now?", tool="get_weather",
                     args={"place": "Springfield, IL", "days": 1})
    local(_reply(content="The weather service is currently down for maintenance. I'll try again later."))
    with pytest.raises(LocalFailure, match="embellished_error"):
        intelligence.request("fast", "sys", history, SCHEMAS)
    local(_reply(content="The weather service isn't reachable right now, sir. I'll try again in a moment."))
    out = intelligence.request("fast", "sys", history, SCHEMAS)
    assert out.content[0].text.startswith("The weather service isn't reachable")
    local(_reply(content="I can't reach the weather service right now. Shall I check the forecast page instead?"))
    assert intelligence.request("fast", "sys", history, SCHEMAS).content[0].text.startswith("I can't reach")


def test_streamed_speech_never_says_a_pc_reading_nothing_measured():
    """Known issue 31 on the streamed path: parse_reply refused 'Your RAM is at 41 percent, sir.' (unchecked_reading),
    but the stream had already said it, and the turn kept it as the answer."""
    from brain import llm
    said = []
    live = llm._SpokenAsWritten(said.append, unchecked="and the RAM?")
    live.feed("Your RAM is at 41 percent, sir. Your CPU is running cool right now. Chrome is using most of it. ")
    assert said == ["Chrome is using most of it."]
    measured = []
    llm._SpokenAsWritten(measured.append).feed("Your RAM is at 41 percent, sir. ")    # after a skill measured it
    assert measured == ["Your RAM is at 41 percent, sir."]


def test_a_worded_reply_to_a_pc_reading_question_streams_no_invented_reading(local):
    from brain import llm
    local(_reply(content="Your RAM is at 41 percent, sir. Chrome is using most of it."))
    said = []
    live = llm._SpokenAsWritten(said.append, unchecked="and the RAM?")
    history = [{"role": "user", "content": "how's my PC doing?"}, {"role": "assistant", "content": "All normal, sir."},
               {"role": "user", "content": "and the RAM?"}]
    out = intelligence.request("fast", "sys", history, [], on_text=live.feed)     # no tools: the reply streams
    assert "41" not in " ".join(said) and "41" not in out.content[0].text
    heard = []
    feed = intelligence._past_the_opening(heard.append, False, said="and the RAM?")
    feed("Your RAM is at 41 percent, sir. Chrome is using most of it. ")
    assert "41" not in " ".join(heard)
