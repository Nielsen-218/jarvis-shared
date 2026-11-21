"""Defects the owner's first real voice talk (2026-11-15 ~09:40, data/jarvis.log) showed and the typed tests had not:

1. "So people are saying ... What is going on?" and "So, um, so I heard ... Is that true?": speech fillers kept the claim check from firing.
2. "Open the mesh panel in the blender sidebar. Press N. Click Bridge On.": the recogniser writes a spoken list as sentences; the steps were
   not a plan, so Jarvis opened Blender and started clicking instead of saying the steps back and asking once.
3. "Yes." to "Want me to check what the official advice says?": the reply said "I checked the official UK government advice" with no search.
4. "Can you make me a humanoid in Blender?" -> "Yes." -> "Give me a moment while I work on it ... I'll let you know when it's ready", then
   "Are you working on it?" -> "Yes, I'm working on generating the humanoid": nothing was running.
5. "What's your name?" -> seven sentences."""
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from core import intelligence, pcsteps, quick, webintent

# ---- 5 ---------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("said", ["What's your name?", "what is your name", "So, what's your name?", "What should I call you?",
                                  "Jarvis, what's your name, sir?", "Do you have a name?"])
def test_the_name_question_gets_one_short_line(said):
    reply = quick.handle(said)
    assert reply and reply.startswith("Jarvis,") and len(reply.split()) <= 4, reply


@pytest.mark.parametrize("said", ["Who are you?", "who are you exactly", "Introduce yourself.", "Well, who are you?"])
def test_the_who_question_gets_one_short_line(said):
    reply = quick.handle(said)
    assert reply and reply.startswith("I'm Jarvis") and reply.count(".") == 1, reply


@pytest.mark.parametrize("said", ["What is your name for the file?", "Who are you calling?", "Who are you meeting tomorrow?",
                                  "What's your favorite color?", "What's the name of this song?"])
def test_other_questions_are_not_identity_questions(said):
    assert not (quick.handle(said) or "").startswith(("Jarvis,", "I'm Jarvis"))


# ---- 2 ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["Open the mesh panel in the blender sidebar. Press N. Click Bridge On.",
                                  "Open the Meshy panel in the Blender sidebar. Press N. Click Bridge On.",
                                  "Open the Meshy panel in the Blender sidebar, press N, click Bridge On."])
def test_a_spoken_list_of_steps_is_one_plan_that_asks_once(said, monkeypatch):
    # Blender counts as installed here whatever this PC has (it was uninstalled 2026-11-20 and these tests failed)
    from pathlib import Path
    from tools import apps as _apps
    real = _apps._installed
    monkeypatch.setattr(_apps, "_installed", lambda: {**real(), "blender 5.2": Path(r"C:\Program Files\Blender Foundation\Blender 5.2lender-launcher.exe")})
    plan = pcsteps.plan_for(said, [], "owner")
    assert plan and plan["review"] is True and plan["steps"][0]["tool"] == "use_app" and "blender" in plan["app"].casefold()
    assert "press N" in plan["steps"][0]["arguments"]["goal"] and "Bridge On" in plan["steps"][0]["arguments"]["goal"]


@pytest.mark.parametrize("said", ["Open Blender. Then close it.", "Open Blender. How are you?", "Open Blender. Wait.", "Press N. Never mind.",
                                  "I opened the panel. Then I pressed N."])
def test_sentences_that_are_not_all_ui_steps_are_not_a_plan(said):
    assert pcsteps.split(said) == []


# ---- 1 ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, query", [
    ("So people are saying the government wants everyone to bunker down and buy food. What is going on?",
     "the government wants everyone to bunker down and buy food news"),
    ("So, um, so I heard Iran struck a base in Bahrain. Is that true?", "Iran struck a base in Bahrain news"),
    ("Well, they say the president signed a tariff bill. Is that true?", "the president signed a tariff bill news"),
    ("Um, I heard that NATO sanctions on Russia were approved. Is that true?", "NATO sanctions on Russia were approved news"),
])
def test_speech_fillers_do_not_stop_the_claim_check(said, query):
    assert webintent.kind(said) == "news"
    assert webintent.news_query(said) == query


@pytest.mark.parametrize("said", ["So, um, they say the police arrested Tyler. What is going on?", "Well, I heard my sister got fired. Is that true?",
                                  "Um, so, they say the shooting happened at Springfield High School. Is that true?",
                                  "So people say the update crashed. What is going on?"])
def test_fillers_open_no_door_for_private_claims(said):
    assert webintent.kind(said) != "news", said


# ---- 4 ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["I'm working on generating the humanoid model for you right now.", "Give me a moment while I work on it.",
                                  "I'll let you know when it's ready.", "I'll generate the mesh and rig it locally.", "I'm rendering it now, sir.",
                                  "I'm working on it, sir."])
def test_work_in_progress_nothing_is_doing_is_a_promise_without_a_call(said):
    assert intelligence.promises_action(said)


@pytest.mark.parametrize("said", ["Here is what the sources say.", "The model is generating slowly.", "I am doing fine, thanks.",
                                  "Blade Runner's style was ahead of its time.",
                                  "It may take a couple of minutes for the encoder to finish.", "I'm working on being a better assistant.",
                                  "If you like, I'll generate the mesh.",
                                  "I'll generate the mesh if you say so.", "That will take a bit of practice.",
                                  "I'll let you know when it's ready, if you'd like.", "Shall I start building it for you?",
                                  "I can make you a humanoid in Blender right now, sir."])
def test_plain_statements_offers_and_estimates_are_not_promises(said):
    assert not intelligence.promises_action(said)
    assert not intelligence.claims_done(said)          # the widening never reaches claims_done (chat turns, failed tools)


@pytest.mark.parametrize("said, text", [
    ("I'm cooking pasta tonight.", "Nice. It will take a few minutes to boil, then you can add the sauce."),
    ("Why is my video slow?", "It may take a couple of minutes for the encoder to finish."),
    ("How does Blender do renders?", "This will take a moment on weak GPUs."),
    ("How are you?", "I'm doing well, sir."),
])
def test_chat_answers_with_an_estimate_are_still_said(said, text):
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}
    message = intelligence.parse_chat_reply(response, said, "chat")
    assert text.split(".")[0].split()[0] in " ".join(b.text for b in message.content)


# ---- 3 and 4 through the Brain ----------------------------------------------------------------------------------------------------
@pytest.fixture
def brain(monkeypatch):
    from brain import llm
    from core import pcfast, websearch
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    state = SimpleNamespace(replies=[], tools=[])

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        text = state.replies.pop(0) if state.replies else "Sources say it is a preparedness campaign (gov.uk, 2026-11-10)."
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")

    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(registry, "run", lambda name, args: (state.tools.append((name, dict(args))),
                                                             ("1. Be Ready campaign (gov.uk, 2026-11-10)", False))[1])
    monkeypatch.setattr(websearch, "LAST", {})
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.state = state
    return b


def _searches(brain):
    return [c for c in brain.state.tools if c[0] in ("search_internet", "research_online")]


def test_a_yes_to_check_the_advice_runs_the_search_it_claims(brain):
    brain.state.replies = ["That is a legitimate UK Be Ready campaign, not panic. Want me to check what the official advice says for your area?",
                           "I checked the official UK government advice and it is a standard preparedness campaign."]
    brain.ask("So people are saying the government wants everyone to bunker down and buy food. What is going on?", [].append)
    first = len(_searches(brain))
    assert first >= 1, "the claim itself was not searched"
    said = []
    brain.ask("Yes.", said.append)
    assert len(_searches(brain)) > first, "his yes claimed a check and ran none"


def _parsed(text, said="Yes."):
    """What intelligence.parse_reply (the real reply filter every local reply passes) lets through when no skill ran this turn, with
    nothing_ran and idle worked out the way brain.llm does (`nothing_ran` is False for a question: a status question is one)."""
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}
    try:
        message = intelligence.parse_reply(response, set(), said=said, nothing_ran=not intelligence.just_asking(said), idle=True)
    except intelligence.LocalFailure:
        return None                      # the whole reply is refused: the next tier answers, or the failure line
    return " ".join(b.text for b in message.content if getattr(b, "type", "") == "text")


@pytest.mark.parametrize("said", ["Yes.", "Are you working on it?", "What's the estimate time?", "Is it ready yet?"])
@pytest.mark.parametrize("text", [
    "I'll generate the mesh and rig it locally. Give me a moment while I work on it. I'll let you know when it's ready.",
    "Yes, I'm working on generating the humanoid model for you right now.",
    "I'm working on it, sir."])
def test_a_promised_build_with_no_tool_is_never_said_as_progress(text, said):
    assert _parsed(text, said) is None


def test_an_offer_and_a_plain_answer_still_pass():
    assert _parsed("I can make you a humanoid in Blender right now, sir. Shall I start building it for you?")
    assert _parsed("Here is what the sources say.")


# ---- review findings 2026-11-15 (round 2) -----------------------------------------------------------------------------------------
def test_a_taught_phrase_beats_the_built_in_identity_answer(monkeypatch):
    from core import commands
    monkeypatch.setattr(commands, "handle", lambda raw: "Friday, sir." if raw.lower().startswith("who are you") else None)
    assert quick.handle("Who are you?") == "Friday, sir."


@pytest.mark.parametrize("said", ["Open the Blender sidebar. Click Save? Press N.", "Click Save? Click Cancel. Press N."])
def test_a_question_mark_inside_the_list_is_no_plan(said):
    assert pcsteps.split(said) == []


def test_a_list_too_long_for_the_goal_is_no_plan():
    said = "Open the Blender sidebar. " + " ".join(f"Press {k}." for k in "ABCDEFGHIJKLMNOPQRSTUVWXYZ") * 3
    assert pcsteps.split(said) == []


def test_fillers_before_the_empty_question_itself(monkeypatch):
    assert webintent.lookup_query("They say the president signed a tariff bill. Um, is that true?") == "the president signed a tariff bill news"


def test_an_offer_about_another_subject_does_not_rerun_the_old_search():
    from core import chatturn
    history = [{"role": "user", "content": "What did the president say about tariffs?"},
               {"role": "assistant", "content": "He said they will rise. Want me to check what the local government says?"},
               {"role": "user", "content": "Yes."}]
    assert chatturn.yes_reissues(history, web=True) == ""


def test_the_search_after_a_yes_uses_the_line_the_yes_answers(brain):
    brain.state.replies = ["That is a legitimate UK Be Ready campaign. Want me to check what the official advice says for your area?"]
    brain.ask("So people are saying the government wants everyone to bunker down and buy food. What is going on?", [].append)
    brain.messages.append({"role": "user", "content": "Yes."})        # as in a real turn: his yes is the newest message
    brain._idle_for = 1.0                                               # his yes comes a second after the offer
    assert brain._yes_line("Yes.") == "So people are saying the government wants everyone to bunker down and buy food. What is going on?"
    brain._idle_for = 500.0
    assert brain._yes_line("Yes.") == "Yes."                       # an old offer is not what this yes answers


# ---- live check 2026-11-15 10:32 (deployed fixes, asked through the chat path) + the review of it ---------------------------------------
@pytest.fixture
def idle_pc(monkeypatch):
    """This is the desk PC and nothing known is running."""
    from core import craftjobs, missions, pcbridge, progress
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(missions, "listing", lambda active_only=False, limit=20: [])
    monkeypatch.setattr(craftjobs, "get", lambda job_id=None: None)
    monkeypatch.setitem(progress._now, "what", "")


@pytest.mark.parametrize("said", ["Are you working on it?", "Are you still working on that?", "Did you finish that?", "Well, are you done with it?",
                                  "Hey Jarvis, are you working on it, sir?", "Any progress on it?", "What's the status of that?"])
def test_a_status_question_about_it_with_nothing_running_is_answered_in_code(said, idle_pc):
    reply = quick.handle(said)
    assert reply and reply.startswith("Nothing is running that I know of"), reply


@pytest.mark.parametrize("running", ["mission", "craft job", "pc task"])
def test_a_status_question_while_anything_known_runs_is_not_answered_here(running, idle_pc, monkeypatch):
    from core import craftjobs, missions, progress
    if running == "mission":
        monkeypatch.setattr(missions, "listing", lambda active_only=False, limit=20: [{"id": "m1", "goal": "scan the PC"}])
    elif running == "craft job":
        monkeypatch.setattr(craftjobs, "get", lambda job_id=None: {"state": "running", "label": "rigging the knight"})
    else:
        monkeypatch.setitem(progress._now, "what", "opening Blender")
    assert quick.handle("Are you working on it?") is None


def test_the_laptop_never_says_nothing_is_running_by_itself(idle_pc, monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: None)           # the PC had nothing to say about it: the laptop never invents the answer
    assert quick.handle("Are you working on it?") is None


@pytest.mark.parametrize("said", ["How's it going?", "Hey Jarvis, how's it going?", "Is it working?", "Is it done?", "Are you ready?", "Are you busy?",
                                  "Are you working on the weather?", "What's the status of my order?", "Is it ready to deploy?", "Did you finish the book?",
                                  "Any progress?", "Have you started?"])
def test_greetings_and_other_questions_are_not_status_questions(said, idle_pc):
    assert not (quick.handle(said) or "").startswith("Nothing is running")


@pytest.mark.parametrize("text", ["I'm making a humanoid character in Blender now, sir.", "I am making a new rig for you.", "I'm making it now."])
def test_making_something_with_no_tool_is_progress_that_is_not_happening(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I can make a new humanoid character in Blender for you, sir.", "I am making it clear that this is not medical advice.",
    "I'm making a note of that.", "I'm making a point of being honest.", "I'm making an exception for you.",
    "I'm making a guess here.",
    "I am working on the assumption that you meant tonight.", "I'm making progress."])
def test_ordinary_first_person_sentences_are_not_progress_claims(text):
    assert not intelligence.promises_action(text)
    if intelligence.claims_done(text):
        return                      # an older claims_done pattern (I'm building/creating ...) judges these: not this change
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}
    message = intelligence.parse_chat_reply(response, "How are you?", "chat")
    assert text.split()[0] in " ".join(b.text for b in message.content)


@pytest.mark.parametrize("said", ["Yes, open it.", "open it", "Sure, open them.", "launch that", "please open it up", "Can you open it?",
                                  "Could you launch that?", "go ahead and open it", "I want you to open it"])
def test_the_semantic_planner_invents_no_target_for_a_pronoun(said):
    from core import pcsemantic
    assert pcsemantic.interpret(said) is None


# ---- the last live check (11:18): the 9B's other ways to say "it is under way" -------------------------------------------------------
@pytest.mark.parametrize("text", [
    "I am starting to generate a new humanoid character in Blender for your Shadow World project, sir.",
    "I will work on it locally and let you know when it is ready.", "Yes, sir. It will appear in Blender shortly.", "It will be ready shortly.",
    "I am opening Blender now to start on your humanoid, sir.", "It will take a moment to finish rigging and posing the humanoid.",
    "I am opening Blender to start on your new humanoid, sir."])
def test_the_other_ways_to_say_it_is_under_way_are_promises_without_a_call(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I'm doing fine, sir.", "Going well, sir. Just here for you.", "I am making it clear that this is not medical advice.", "It will be sunny tomorrow, sir.",
    "The package will appear on Tuesday.", "It will take a moment to boil.", "I'm starting to think you're right.", "I am about to leave the topic."])
def test_ordinary_sentences_with_the_same_words_are_not(text):
    assert not intelligence.promises_action(text)


def test_a_queued_mission_is_not_work_being_done(idle_pc, monkeypatch):
    from core import missions
    monkeypatch.setattr(missions, "listing", lambda active_only=False, limit=20: [{"id": "m1", "goal": "test", "state": "queued"}])
    assert quick.handle("Are you working on it?").startswith("Nothing is running that I know of")
    monkeypatch.setattr(missions, "listing", lambda active_only=False, limit=20: [{"id": "m1", "goal": "scan", "state": "running"}])
    assert quick.handle("Are you working on it?") is None


# ---- the 11:50 live check: more of the 9B's ways to say "it is under way" ----------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Give me a moment while the mesh builds in the background.", "I'm checking on the character job now, sir. It's running in the background.",
    "Got it, sir. It'll be rigged and ready to open when it's done.", "Yes, sir. It'll be rigged and ready to open when it's done.",
    "I'll let you know when it's finished."])
def test_more_ways_to_say_the_build_is_under_way_are_promises_without_a_call(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "Got it, sir.", "It'll be sunny tomorrow.",
    "I'll run it in the background if you like.", "That will be a good plan.", "Dinner will be ready at six.", "Yes, sir.",
    "The file is ready when you are.", "The music keeps playing in the background."])
def test_ordinary_sentences_that_share_those_words_are_not(text):
    assert not intelligence.promises_action(text)


# ---- the 12:17 live check: a promised RESULT, whoever the subject (grammar-level rule) ---------------------------------------------------
@pytest.mark.parametrize("text", [
    "I will work on it locally without needing internet or credits.", "Give me a moment and I will let you know when the mesh is ready.",
    "I will let you know when the humanoid is ready.", "Yes, sir. It will take a few minutes to complete.", "It should be ready in a few minutes.",
    "It will be ready shortly.", "It'll be rigged and ready to open when it's done.", "The render should be finished in a moment."])
def test_a_promised_result_is_refused_without_a_call_whatever_the_subject(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "Dinner will be ready at six.", "Your order will be done by Friday.", "The meeting will be available on Monday.", "That will be a good plan.",
    "The file is ready when you are.", "It will take a moment to boil.", "It will take a few minutes to walk there.", "The package will be there tomorrow.",
    "It should be fine.", "That should work, sir.", "You will be ready for the exam.", "You'll be ready by Friday.", "We will be ready at noon."])
def test_plans_about_the_world_and_other_people_are_not_promises_of_work(text):
    assert not intelligence.promises_action(text)


# ---- the 12:46 live check: the line said after a refused promise --------------------------------------------------------------------
def test_a_reply_refused_for_promising_work_ends_in_an_honest_line_not_a_lost_train_of_thought():
    from core import pchelp
    intelligence.take_refused_promise()                                     # clear any earlier note
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                "content": "I will let you know when the humanoid is ready. It should be ready in a few minutes."}}]}
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(response, "Yes.", "chat")
    line = pchelp.model_silent("sir")
    assert line.startswith("I haven't started that, sir") and "pretend" in line
    assert pchelp.model_silent("sir").startswith("I lost my train of thought")      # the note is used once


def test_a_model_that_simply_said_nothing_keeps_the_old_line():
    from core import pchelp
    intelligence.take_refused_promise()
    assert pchelp.model_silent("sir").startswith("I lost my train of thought")


def test_a_command_reply_refused_for_a_promise_ends_in_the_honest_line_too():
    from core import pchelp
    intelligence.take_refused_promise()
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "I will work on it locally. I will let you know when it is ready."}}]}
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_reply(response, set(), said="Yes.", nothing_ran=True, idle=True)
    assert pchelp.model_silent("sir").startswith("I haven't started that")


@pytest.mark.parametrize("text", ["I am working on a new humanoid character for your Shadow World project, sir.", "I'm working on the model now.",
                                  "I am working on a rig for you."])
def test_working_on_a_named_deliverable_is_progress_that_is_not_happening(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", ["I am working on the assumption that you meant tonight.",
                                  "I'm working on being a better assistant."])
def test_other_working_on_sentences_pass(text):
    assert not intelligence.promises_action(text)


# ---- the 13:15 live check: an example line of personality.md parroted to an unrelated line --------------------------------------------------
@pytest.mark.parametrize("sentence, said, parrot", [
    ("Running smoothly, sir.", "Hey Jarvis. Can you make me a humanoid in Blender?", True),
    ("Running smoothly, sir.", "Tell me what the news is about the election in the capital.", True),
    ("Any time, sir.", "Can you open Blender and set up the scene for the character?", True),
    ("Running smoothly, sir.", "How are you doing?", False), ("Running smoothly, sir.", "How are you?", False),
    ("Any time, sir.", "Thank you.", False), ("Any time, sir.", "Thanks, Jarvis.", False),
    ("It is running smoothly, sir.", "How is the PC doing right now?", False),
    ("Fine, sir.", "Can you make me a humanoid in Blender?", False)])
def test_an_example_reply_said_to_an_unrelated_line_is_a_parrot(sentence, said, parrot):
    assert intelligence.parrots_example(sentence, said) is parrot


def test_the_chat_and_command_parsers_refuse_the_parrot():
    said = "Hey Jarvis. Can you make me a humanoid in Blender?"
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Running smoothly, sir."}}]}
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(response, said, "chat")
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_reply(response, set(), said=said, nothing_ran=False, idle=False)
    ok = intelligence.parse_chat_reply(response, "How are you doing?", "chat")
    assert "Running smoothly" in " ".join(b.text for b in ok.content)


# ---- the 13:43 live check: "I'm already on it" and the opening-verb form --------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "I'm already on it, sir.", "I'm on it, sir.", "Generating a new humanoid character with rigging for your 'Shadow World' project right now.",
    "I am drawing a humanoid character in T-pose and rigging it locally right now.", "Yes, sir. I'm already on it. Rigging the character now."])
def test_the_last_wordings_of_a_build_under_way_are_refused_without_a_call(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "Building a case takes time, sir.", "Generating electricity is expensive.", "Creating a habit takes about two months.", "Rendering is GPU heavy.",
    "I'm on the phone with the plumber, sir.", "I'm on your side, sir.", "Drawing is a skill you build over time.", "Modeling clay is cheap.",
    "Sure, I can generate a humanoid if you like.", "That's a good plan for a humanoid character."])
def test_ordinary_sentences_with_those_verbs_still_pass(text):
    assert not intelligence.promises_action(text)


# ---- the 14:19 live check (laptop): the stall and "have it ready" in any form ---------------------------------------------------------------
@pytest.mark.parametrize("text", ["I'll have it ready for you shortly.", "Give me a moment to get the scene ready.", "I'll report back once the model is ready.",
                                  "Give me a second.", "I'll get everything set up.", "Getting the scene ready now."])
def test_the_stall_and_have_it_ready_are_refused_without_a_call(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", ["Give me a second to think about that.", "I'll have a look at that if you like.", "I'll report the numbers if you want them.",
                                  "Sure, I can set it up if you say so.", "I'll have it ready when you say go.", "The scene is ready when you are.",
                                  "I'll have it ready whenever you tell me."])
def test_offers_and_conditions_still_pass(text):
    assert not intelligence.promises_action(text)


# ---- the 14:56 live check (laptop): a compound promise, and a stall next to an offer-looking word ----------------------------------------------
@pytest.mark.parametrize("text", [
    "Give me a moment to check if there's already a file you want me to use, or I'll create a new one from scratch.",
    "I'll use a trusted template and save the file to your project folder.", "I will pull the template and then save the file."])
def test_a_compound_promise_and_a_stall_beside_an_offer_looking_word_are_refused(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", ["Give me a second to think about that.", "I'll think about it and get back to you.", "I'll check and let you know if you like.",
                                  "I'll open it if you say so.", "Sure, I'll save it when you say go.", "I'll be here and ready, sir.",
                                  "If you want, I'll create a new one from scratch.", "Shall I create a new one from scratch?"])
def test_real_offers_still_pass_after_that(text):
    assert not intelligence.promises_action(text)



# ---- THE CLASS (14:56 live check): with no tool, "I am <doing something>" is a claim, in any wording --------------------------------------------------
@pytest.mark.parametrize("text", [
    "I am starting a new humanoid character generation now.", "I am starting a new humanoid character generation in Blender for your Shadow World project, sir.",
    "One sec while it builds the mesh and sets up the bones.", "I am drawing a humanoid.", "I'm opening Blender.", "I'm pulling up the report now.",
    "I'm checking on that for you.", "I am creating a new humanoid character from scratch now, sir.",
    # figurative first-person progressives are dropped too, by design (Jarvis does not talk like this; a real action has a tool)
    "I'm cooking pasta tonight.", "I'm making dinner tonight, sir.", "I'm building a case for why you should rest.", "I'm preparing some thoughts on that.",
    "I'm processing a lot right now, sir.", "I'm working on a project with my team."])
def test_any_i_am_doing_something_is_refused_when_no_tool_ran(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I'm listening, sir.", "I'm thinking about it.", "I'm not sure.", "I'm sorry about that, sir.", "I'm here, sir.", "I'm happy to help.", "I'm doing fine, sir.",
    "I'm going to need a bit more detail.", "I am making sure I understood: you want X.", "I'm making progress on understanding you.", "I'm afraid I can't do that.",
    "I'm starting to think you're right.", "I'm wondering whether you meant tonight.", "I'm looking forward to it, sir.", "I'm at your service.", "I'm fine, sir.",
    "I'm ready when you are.", "I'm trying to understand.", "I am sure of it.", "I'm well, thank you.", "I'm working on being a better assistant.",
    "I am working on the assumption that you meant tonight."])
def test_how_jarvis_feels_or_thinks_is_not_a_claim_of_work(text):
    assert not intelligence.promises_action(text)


# ---- the 15:54 live check (laptop): a status question is the PC's to answer; an opening gerund with no "I am" is a claim -------------------------
def test_the_laptop_asks_the_pc_for_status_instead_of_the_model_saying_yes(monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    seen = []
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: (seen.append(raw), "Nothing is running that I know of, sir. Tell me what you want done and I'll do it.")[1])
    assert quick.handle("Are you working on it?").startswith("Nothing is running that I know of")
    assert seen == ["Are you working on it?"]


def test_when_the_pc_says_it_is_not_a_pc_matter_the_laptop_leaves_it_to_the_model(monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: None)
    assert quick.handle("Are you working on it?") is None


def test_an_unreachable_pc_is_said_not_guessed_for_a_status_question(monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: "I can't reach the PC right now, so I can't check that, and I won't guess.")
    assert "can't reach the PC" in quick.handle("Are you working on it?")


@pytest.mark.parametrize("text", ["Checking Blender and Shadow World status, sir.", "Opening the scene now.", "Reading your files, sir.",
                                  "Quite, sir. Checking the status."])
def test_an_opening_gerund_with_no_tool_is_a_claim(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", ["Reading is good for you.", "Opening hours are nine to five.", "Running is healthy.", "Searching for meaning, sir?",
                                  "Checking in, sir.", "Starting with the basics: what do you need?", "Looking at it again, I think you're right."])
def test_an_opening_gerund_that_is_not_work_passes(text):
    assert not intelligence.promises_action(text)
