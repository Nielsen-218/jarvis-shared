"""Master conversationalist (2026-11-15, builder B): the owner's own failures of 2026-11-14 as tests.
The model is scripted with what it really wrote that evening (data/jarvis.log, read-only); the rest is the real Brain."""
import pytest

from core import speech
from tests.replay_brain import Replay

STEP4 = ("I’m ready to follow step four, sir—but I need to know which guide you’re following. "
         "Is it a Blender plugin install, a tutorial page, or something else? "
         "Tell me the first thing you want me to do in that step, and I’ll handle it right away.")
STEP4_SENTENCES = ("ready to follow step four", "Blender plugin install", "first thing you want me to do")


def _has_step4(sentences) -> bool:
    return any(part in " ".join(sentences) for part in STEP4_SENTENCES)


# ---- (a) the same reply, again --------------------------------------------------------------------------------------------
def test_a_reply_he_already_heard_two_exchanges_ago_is_not_said_a_third_time(monkeypatch):
    """15801 and 15820 of data/jarvis.log: 'Open. Control. Center.' and 'Okay. In Blender.' were each answered with the
    same three sentences about step four (and the one more try wrote them again)."""
    r = Replay(monkeypatch, {"Open. Control. Center.": STEP4, "Okay. In Blender.": STEP4})
    first = r.say("Open. Control. Center.")
    assert _has_step4(first)
    second = r.say("Okay. In Blender.")
    assert not _has_step4(second), second
    assert len(second) == 1 and "same answer" in second[0], second
    stored = r.brain.messages[-1]["content"]
    stored = stored if isinstance(stored, str) else " ".join(b.text for b in stored)
    assert stored == second[0], "history keeps what was said"


def test_the_one_more_try_is_used_when_it_works(monkeypatch):
    other = "Got it, sir: a Blender plugin. Open its panel in the sidebar and tell me what you see."
    said_other = ["Got it, sir: a Blender plugin.", "Open its panel in the sidebar and tell me what you see."]
    r = Replay(monkeypatch, {"Open. Control. Center.": STEP4, "Okay. In Blender.": [STEP4, other]})
    r.say("Open. Control. Center.")
    assert r.say("Okay. In Blender.") == said_other


def test_asking_for_it_again_gets_it_again(monkeypatch):
    r = Replay(monkeypatch, {"Open. Control. Center.": STEP4, "Say that again.": STEP4})
    r.say("Open. Control. Center.")
    assert _has_step4(r.say("Say that again."))


def test_an_older_reply_said_for_an_older_statement_is_stale_to_the_polisher():
    earlier = [("Open. Control. Center.", STEP4), ("Hello there.", "Hello, sir.")]
    said = []
    p = speech.Polisher(said.append, owner=True, title="sir", asked="Okay. In Blender.", earlier=earlier, retry=True)
    p(STEP4.split(". ")[0] + ".")
    assert p.stale and said == []


# ---- (c) a bare yes, no or 'I don't see it' belongs to what he was just told or asked -----------------------------------------
from core import chatturn  # noqa: E402

DASH = [{"role": "user", "content": "Open Dashboard."},
        {"role": "assistant", "content": "The Command Center is up on your screen, sir."},
        {"role": "user", "content": "I don't see it."},
        {"role": "assistant", "content": "I don't see a Dashboard on your screen, sir. Would you like me to open it now?"}]
HEADLINES = [{"role": "user", "content": "Do your research and tell me what the news is saying about that."},
             {"role": "assistant", "content": "Those claims are circulating on social media, not official guidance. "
                                              "If you\u2019d like, I can show you the actual headlines from trusted sources so you "
                                              "can see what\u2019s really being reported."}]


def _h(history, said):
    return history + [{"role": "user", "content": said}]


@pytest.mark.parametrize("history, said, offer, before", [
    (DASH, "Yes.", "Would you like me to open it now?", "Open Dashboard."),
    (DASH, "Yes please.", "Would you like me to open it now?", "Open Dashboard."),
    (HEADLINES, "Yes, I would like that very much.", "I can show you the actual headlines", "what the news is saying"),
    (HEADLINES, "Sure, go ahead.", "I can show you the actual headlines", ""),
])
def test_his_yes_is_bound_to_the_offer_just_made(history, said, offer, before):
    note = chatturn.binding_note(_h(history, said))
    assert offer in note and before in note and "yes" in note.casefold(), note
    assert "do not ask it again" in note.casefold()


@pytest.mark.parametrize("said", ["No.", "No thanks.", "Nah, leave it."])
def test_his_no_drops_the_offer(said):
    note = chatturn.binding_note(_h(DASH, said))
    assert "Would you like me to open it now?" in note and "drop it" in note.casefold(), note


def test_i_dont_see_it_is_about_what_was_just_said_done():
    h = DASH[:2]
    for said in ("I don't see it.", "It's not there.", "Nothing happened.", "It didn't open."):
        note = chatturn.binding_note(_h(h, said))
        assert "The Command Center is up on your screen" in note and "do not say it again" in note.casefold(), (said, note)


@pytest.mark.parametrize("history, said", [
    (DASH, "Yes, open the dashboard."),                                           # words of his own: a request
    (DASH, "Yes, but only the second one"),
    (DASH[:2], "Yes."),                                                           # nothing was asked or offered
    ([{"role": "user", "content": "What time is it?"}, {"role": "assistant", "content": "It's 3:32 PM, sir."}], "Okay."),
    (DASH, "I don't see it."),                                                    # an answer to the question just asked
    ([], "Yes."),
    (DASH[:2], "Open the Command Center again."),
])
def test_nothing_is_bound_where_nothing_was_asked(history, said):
    assert chatturn.binding_note(_h(history, said)) == ""


def _seed(r, pairs):
    import time
    r.brain._last_turn = time.monotonic()          # a conversation in progress (not archived as long idle)
    for q, a in pairs:
        r.brain.messages.append({"role": "user", "content": q})
        r.brain.messages.append({"role": "assistant", "content": a})


def _last_user_part(body):
    newest = body["messages"][-1]
    return newest["content"] if isinstance(newest["content"], str) else " ".join(p.get("text", "") for p in newest["content"])


@pytest.mark.parametrize("said", ["Yes.", "Yes, I would like that very much."])
def test_the_model_is_told_what_his_yes_answers_on_every_path(monkeypatch, said):
    r = Replay(monkeypatch, {said: "On it, sir."})
    _seed(r, [("Open Dashboard.", "The Command Center is up on your screen, sir."),
              ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")])
    r.say(said)
    assert r.bodies and all("Would you like me to open it now?" in _last_user_part(b) for b in r.bodies), \
        [_last_user_part(b)[-200:] for b in r.bodies]
    assert all(_last_user_part(b).count("Their line is a yes") == 1 for b in r.bodies)


def test_nothing_is_added_to_a_line_that_answers_nothing(monkeypatch):
    r = Replay(monkeypatch, {"Yes.": "Sir?"})
    _seed(r, [("What time is it?", "It's 3:32 PM, sir.")])
    r.say("Yes.")
    assert r.bodies and not any("Their line" in _last_user_part(b) for b in r.bodies)


def test_his_yes_is_not_answered_with_the_same_question(monkeypatch):
    """15762 of data/jarvis.log: 'Yes.' -> "I'm sorry, sir, but I don't see a Dashboard on your screen right now. Would you
    like me to open it?" (the question he had just said yes to)."""
    again = ("I'm sorry, sir, but I don't see a Dashboard on your screen right now. Would you like me to open it?")
    r = Replay(monkeypatch, {"Yes.": again})
    _seed(r, [("Open Dashboard.", "The Command Center is up on your screen, sir."),
              ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")])
    out = r.say("Yes.")
    assert out == ["I'm sorry, sir, but I don't see a Dashboard on your screen right now."], out


def test_a_yes_answered_only_with_its_own_question_hands_the_turn_back(monkeypatch):
    r = Replay(monkeypatch, {"Yes.": "Would you like me to open it?"})
    _seed(r, [("Open Dashboard.", "The Command Center is up on your screen, sir."),
              ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")])
    out = r.say("Yes.")
    assert len(out) == 1 and "won't ask you again" in out[0] and "?" not in out[0], out
    stored = r.brain.messages[-1]["content"]
    assert (stored if isinstance(stored, str) else " ".join(b.text for b in stored)) == out[0], "history keeps what was said"


def test_a_different_question_after_his_yes_is_fine(monkeypatch):
    r = Replay(monkeypatch, {"Yes.": "Which monitor, sir? The left one or the right one?"})
    _seed(r, [("Open Dashboard.", "The Command Center is up on your screen, sir."),
              ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")])
    out = r.say("Yes.")
    assert out and "Which monitor" in " ".join(out)


# ---- (b) what he is "working on", and what Jarvis is "working on" ----------------------------------------------------------
from core import intelligence  # noqa: E402

STATUS_Q = "What's the status on the thing that you were just working on?"


@pytest.mark.parametrize("sentence", [
    "I'm ready to get back to work on 'Shadow World' for your 'Shadow Vanguard' character.",     # 15729
    "I\u2019m checking the status of your Shadow World project and the news situation, sir.",       # 15740
    "I've been holding off until you told me to resume so we can finish the rigging and asset rules without delay.",
])
def test_a_project_named_as_current_work_is_invented_even_when_his_question_says_working_on(sentence):
    """His question shares the word 'work' with the invention, and that used to let it through (the line was said)."""
    assert intelligence.invents_present(sentence, STATUS_Q)
    assert intelligence.chat_unsaid(sentence, STATUS_Q, acts=True)


@pytest.mark.parametrize("sentence, said", [
    ("Since you're working on the boat model, the glue needs a day.", "I'm working on the boat model."),
    ("Yes, the news search is the last thing I did, sir.", STATUS_Q),
    ("Nothing is running right now, sir.", STATUS_Q),
    ("It's three in the afternoon, sir.", "What time is it?"),
])
def test_what_his_own_words_or_a_plain_answer_say_stays(sentence, said):
    assert not intelligence.invents_present(sentence, said)


def test_a_status_question_is_told_to_say_only_what_this_conversation_shows():
    note = chatturn.note(chatturn.CHAT, [{"role": "user", "content": "What's up?"},
                                         {"role": "assistant", "content": "Not much, sir."},
                                         {"role": "user", "content": STATUS_Q}])
    assert "not doing anything between" in note.casefold() and "never name" in note.casefold(), note
    plain = chatturn.note(chatturn.CHAT, [{"role": "user", "content": "How is the weather?"}])
    assert "not doing anything between" not in plain.casefold()


# ---- (d) a request with several steps in one app --------------------------------------------------------------------------------
from core import craftintent, gateway, pcsteps  # noqa: E402


@pytest.fixture
def blender_only(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda name: "Blender" if str(name).strip().casefold() in ("blender", "the blender") else None)
    pcsteps.reset()
    yield
    pcsteps.reset()


OWNER_STEPS = "Open the Meshy panel in the Blender sidebar, press N, click Bridge On."


def test_his_three_steps_are_split_in_order(blender_only):
    assert pcsteps.split(OWNER_STEPS) == ["Open the Meshy panel in the Blender sidebar", "press N", "click Bridge On"]
    assert pcsteps.split("Press N and click Bridge On.") == ["Press N", "click Bridge On"]
    assert pcsteps.split("Okay, I need you to press N, then click Bridge On please") == ["press N", "click Bridge On please"]


@pytest.mark.parametrize("said", [
    "Press N.", "Click Bridge On.", "Open Notepad.", "Open Notepad and type hello.", "How do I press N and click Bridge On?",
    "I'm going to press N and click Bridge On.", "Close Chrome and open Spotify.", "Open the Meshy panel.",
    "Press N and then tell me how the sidebar works.", "What does press N and click do?", "Play some jazz, then turn up the volume.",
])
def test_one_step_or_something_else_is_not_a_list_of_steps(said):
    assert pcsteps.split(said) == []


def test_the_steps_become_one_reviewed_use_app_plan(blender_only):
    found = pcsteps.plan_for(OWNER_STEPS, [], "owner")
    assert found["app"] == "Blender" and len(found["steps"]) == 1
    step = found["steps"][0]
    assert step["tool"] == "use_app" and step["arguments"]["app"] == "Blender"
    assert step["arguments"]["goal"] == "open the Meshy panel in the Blender sidebar; press N; click Bridge On"


def test_the_gateway_restates_the_steps_and_never_runs_them(blender_only):
    decision = gateway._decide(OWNER_STEPS, [])
    assert decision is not None and decision.source == "exact" and [i.kind for i in decision.intents] == ["actions"]
    assert decision.intents[0].steps[0]["tool"] == "use_app"
    assert "press N" in decision.intents[0].summary and "click Bridge On" in decision.intents[0].summary
    from core import authz
    # the gate is core.authz's, unchanged: an app he named himself is his order; one taken from the conversation is shown first
    assert not authz.decide(decision.intents[0].steps, OWNER_STEPS).ask
    assert authz.decide(decision.intents[0].steps, "Press N and click Bridge On.").ask


def test_the_app_comes_from_the_conversation_when_he_does_not_name_it(blender_only):
    history = [{"role": "user", "content": "Okay. In Blender."}, {"role": "assistant", "content": "Which panel, sir?"},
               {"role": "user", "content": "Press N and click Bridge On."}]
    decision = gateway._decide("Press N and click Bridge On.", history)
    assert decision.intents[0].kind == "actions" and decision.intents[0].steps[0]["arguments"]["app"] == "Blender"


def test_no_app_known_asks_one_question_and_his_answer_completes_the_steps(blender_only, monkeypatch):
    monkeypatch.setattr(gateway, "understand", lambda *a, **k: None)          # no planner model in a test
    first = gateway._decide("Press N and click Bridge On.", [])
    assert [i.kind for i in first.intents] == ["clarify"]
    q = first.intents[0].question
    assert q.count("?") == 1 and "Press N" in q and "click Bridge On" in q
    said_so = [{"role": "user", "content": "Press N and click Bridge On."}, {"role": "assistant", "content": q},
               {"role": "user", "content": "In Blender."}]
    second = gateway._decide("In Blender.", said_so)
    assert second.intents[0].kind == "actions" and second.intents[0].values.get("review")
    assert second.intents[0].steps[0]["arguments"] == {"app": "Blender", "goal": "press N; click Bridge On"}
    again = gateway._decide("In Blender.", said_so)
    assert again is None or all(i.kind != "actions" for i in again.intents), "asked once, answered once"


def test_a_stop_or_narration_is_never_taken_for_steps(blender_only):
    for said in ("Stop.", "I'm going to press N and click Bridge On."):
        d = gateway._decide(said, [])
        assert d is None or all(i.kind != "actions" for i in d.intents), said


@pytest.mark.parametrize("said", [
    "Okay, it's a blender plug-in. I need you to open the meshy panel in the Viewpoint sidebar.",    # 15813, taken for a file
    "Open the Meshipano in the viewport sidebar.", "Open the Meshy panel in the sidebar in Blender.",
])
def test_a_panel_is_not_a_file_to_open_in_blender(said):
    assert craftintent.match(said) is None


def test_a_character_file_is_still_opened_in_blender():
    assert craftintent.match("Open it in Blender.") is None or craftintent.match("Open it in Blender.")[0] == "open_in_blender"


@pytest.mark.parametrize("asked", ["Yes, sir?", "What exactly are you doing in Blender?", "How can I help, sir?", "Which one do you mean?"])
def test_a_yes_to_no_yes_or_no_question_binds_to_nothing(asked):
    """From the owner's log: 'Yes.' after the wake answer 'Yes, sir?', and "Let's go." after a what-question."""
    history = [{"role": "user", "content": "Hey Jarvis."}, {"role": "assistant", "content": asked}]
    assert chatturn.binding_note(_h(history, "Yes.")) == ""
    assert chatturn.binding_note(_h(history, "Let's go.")) == ""


def test_the_binding_catches_the_owners_real_cases_and_only_those():
    """The same check run over every YOU line of data/jarvis.log since 2026-11-01 (558 turns): 5 yes, 2 'I don't see it' and
    nothing else; the two that were not his yes ('Yes.' after 'Yes, sir?', 'Let's go.' after a what-question) are the cases above."""
    pairs = [("Should I close Windows Powershell?", "Yes."), ("Would you like me to walk you through it one step at a time?", "Yes."),
             ("Should I open the Meshipano in the viewport sidebar in Blender?", "Yes.")]
    for asked, said in pairs:
        history = [{"role": "user", "content": "Close PowerShell."}, {"role": "assistant", "content": asked}]
        assert asked in chatturn.binding_note(_h(history, said))


def test_the_voice_path_reaches_the_steps_route(blender_only, monkeypatch):
    """pcfast.handle is what Brain._ask calls before the model: it must hand the spoken steps to the gateway's decision."""
    from core import pcbridge, pcfast
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    seen = []
    monkeypatch.setattr(gateway, "execute", lambda decision, said, cancelled=lambda: False, say=None, began=None:
                        seen.append(decision) or "Here's my plan; nothing has run yet.")
    out = pcfast.handle(OWNER_STEPS, [], say=lambda s: None)
    assert seen, out
    assert [i.kind for i in seen[0].intents] == ["actions"] and seen[0].intents[0].steps[0]["tool"] == "use_app"


def test_a_request_the_model_server_refuses_because_of_the_note_is_made_again_without_it(monkeypatch):
    """Live probe 2026-11-15: with the 'I don't see it' note qwen3.5 wrote a tool call Ollama could not parse (HTTP 500) on both
    tiers; the note is only help, so the same request goes once more without it."""
    from core import intelligence
    seen = []

    def fake(body, cancelled=lambda: False, on_text=None, **kw):
        text = body["messages"][-1]["content"]
        text = text if isinstance(text, str) else " ".join(p.get("text", "") for p in text)
        seen.append("Their line says" in text)
        if "Their line says" in text:
            raise intelligence.LocalFailure("local_request_failed")
        return {"model": "m", "choices": [{"message": {"role": "assistant", "content": "Trying again, sir."}, "finish_reason": "stop"}]}
    monkeypatch.setattr(intelligence, "complete_local", fake)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    history = [{"role": "user", "content": "Open Dashboard."}, {"role": "assistant", "content": "The Command Center is up on your screen, sir."},
               {"role": "user", "content": "I don't see it."}]
    out = intelligence.request("fast", "You are Jarvis.", history, [])
    assert seen == [True, False] and out.content[0].text == "Trying again, sir."


# ---- his yes to "Would you like me to open it?" re-issues what he asked for ---------------------------------------------------
@pytest.mark.parametrize("history, said, reissued", [
    (DASH, "Yes.", "Open Dashboard."),
    (DASH, "Yes please.", "Open Dashboard."),
    ([{"role": "user", "content": "Open Spotify."}, {"role": "assistant", "content": "I couldn't find it running. Want me to launch it?"}],
     "Sure, go ahead.", "Open Spotify."),
])
def test_his_yes_to_an_offer_to_open_what_he_asked_for_is_that_request_again(history, said, reissued):
    assert chatturn.yes_reissues(_h(history, said)) == reissued


@pytest.mark.parametrize("history, said", [
    ([{"role": "user", "content": "Close PowerShell."}, {"role": "assistant", "content": "Should I close Windows PowerShell?"}], "Yes."),
    ([{"role": "user", "content": "Delete the old backups."}, {"role": "assistant", "content": "Would you like me to delete them now?"}], "Yes."),
    (DASH, "Yes, open the second one."),                                                  # words of his own
    (DASH[:2], "Yes."),                                                                    # nothing offered
    ([{"role": "user", "content": "What's the weather?"}, {"role": "assistant", "content": "It's sunny. Want me to open the forecast?"}], "Yes."),
])
def test_only_an_offer_to_open_again_what_he_asked_for_is_re_issued(history, said):
    assert chatturn.yes_reissues(_h(history, said)) == ""


def test_the_brain_runs_the_request_again_when_he_says_yes(monkeypatch):
    from core import pcfast
    r = Replay(monkeypatch, {"Yes.": "Sir?"})
    seen = []
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: seen.append(text) or "The Command Center is up on your screen, sir.")
    _seed(r, [("Open Dashboard.", "The Command Center is up on your screen, sir."),
              ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")])
    assert r.say("Yes.") == ["The Command Center is up on your screen, sir."]
    assert seen == ["Open Dashboard."], seen
    assert r.bodies == [], "no model call: the PC path took it"
    users = [m["content"] for m in r.brain.messages if m["role"] == "user"]
    assert users[-1] == "Yes.", "history keeps what he really said"


SEARCHED = [{"role": "user", "content": "Search the web for the latest Blender release"},
            {"role": "assistant", "content": "I found it. If you\u2019d like, I can show you the release details."}]


def test_his_yes_to_an_offer_to_show_what_he_searched_for_is_that_search_again():
    assert chatturn.yes_reissues(_h(SEARCHED, "Yes, I would like that very much."), web=True) == "Search the web for the latest Blender release"
    assert chatturn.yes_reissues(_h(SEARCHED, "Yes, I would like that very much.")) == ""        # not a PC request
    # not for a line that is no web request, nor for an offer that shows nothing
    assert chatturn.yes_reissues(_h([{"role": "user", "content": "Tell me a joke."}, SEARCHED[1]], "Yes."), web=True) == ""
    assert chatturn.yes_reissues(_h([SEARCHED[0], {"role": "assistant", "content": "I found it. Want me to delete the old notes?"}], "Yes."), web=True) == ""


def test_the_brain_runs_that_search_for_his_yes(monkeypatch):
    r = Replay(monkeypatch, {"Yes, I would like that very much.": "Release 5.2 is out, sir."}, shortcuts=False)
    seen = []
    monkeypatch.setattr(r.brain, "_web_request", lambda text, emit: seen.append(text) and False)
    _seed(r, [(SEARCHED[0]["content"], SEARCHED[1]["content"])])
    r.say("Yes, I would like that very much.")
    assert seen == ["Search the web for the latest Blender release"], seen


# ================================ independent review 2026-11-15: the dangerous direction ===========================================
NOT_A_YES = ["Not now.", "Not really.", "Absolutely not.", "Definitely not.", "Certainly not.", "Of course not.", "Do not.", "Let's not.",
             "Sure, not now.", "Why not?", "Thank you.", "Thanks, sir.", "Really?", "Sir.", "Jarvis.", "Okay, not now.", "Please don't.",
             "Yeah, not really.", "No.", "Nope.", "Never mind.", "Wait.", "Hold on.", "Yes, but only the second one.", "Thanks."]
IS_A_YES = ["Yes.", "Yes, please.", "Yeah, open it.", "Yes please.", "Sure.", "Okay.", "Go ahead.", "Do it.", "Absolutely.", "Of course.",
            "Yes, I would like that very much.", "Yes, thank you.", "Sounds good.", "Correct."]


@pytest.mark.parametrize("said", NOT_A_YES)
def test_a_refusal_a_thanks_or_a_name_is_no_yes_and_re_runs_nothing(said):
    assert not chatturn.bare_yes(said)
    history = _h(DASH, said)
    assert chatturn.yes_reissues(history) == "", said
    found = chatturn.binding(history)
    assert not found or found[0] != "yes", (said, found)
    assert "is a yes" not in chatturn.binding_note(history), said


@pytest.mark.parametrize("said", IS_A_YES)
def test_a_real_yes_stays_a_yes(said):
    assert chatturn.bare_yes(said)
    assert chatturn.yes_reissues(_h(DASH, said)) == "Open Dashboard."


@pytest.mark.parametrize("said", ["Not now.", "Not really.", "Absolutely not.", "Of course not.", "Do not.", "Let's not.", "No.", "Nope."])
def test_a_refusal_after_an_offer_drops_it(said):
    found = chatturn.binding(_h(DASH, said))
    assert found and found[0] == "no", (said, found)


@pytest.mark.parametrize("prefix", ["[Spoken into the laptop] ", "[Sent from phone] ", "[Sent from their phone] "])
def test_the_channel_prefix_does_not_hide_his_yes(prefix):
    h = _h(DASH, prefix + "Yes.")
    assert chatturn.binding(h)[0] == "yes" and chatturn.yes_reissues(h) == "Open Dashboard."
    assert chatturn.binding(_h(DASH, prefix + "Not now."))[0] == "no"


def H(*turns):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(turns)]


@pytest.mark.parametrize("turns", [
    ("Open Dashboard.", "Done. Would you like me to open the Command Center too?", "Yes."),
    ("Open Chrome.", "Opened.", "Thanks.", "You're welcome. Want me to open Spotify?", "Yes."),
    ("Open Chrome.", "Opened.", "Okay.", "Want me to open Spotify?", "Yes."),
    ("Open Chrome.", "Opened.", "What's the weather?", "Sunny. Want me to open the weather app?", "Yes."),
    ("Start deleting my temp files.", "I can't delete without approval. Would you like me to open the folder?", "Yes."),
    ("Show me the email draft.", "Here it is. Shall I open the send dialog?", "Yes."),
    ("Open notepad and type my password.", "I don't see notepad. Would you like me to open it?", "Yes."),
    ("Open the browser and click delete account.", "The browser is open. Should I open the settings page?", "Yes."),
    ("Show me how to delete the file.", "Sure. Want me to show you the steps?", "Yes."),
    ("Why would I open the door?", "Because. Want me to open it?", "Yes."),
    ("I opened the door earlier.", "Nice. Would you like me to open it?", "Yes."),
    ("Open the garage.", "The garage is open. Do you want me to open the lights?", "Yes please."),
])
def test_a_yes_re_runs_nothing_when_the_offer_is_about_something_else_or_the_request_was_not_simple(turns):
    """The reviewer's inputs: the object of the offer must be the thing he asked for, the request one plain open, and no other
    request of his in between."""
    assert chatturn.yes_reissues(H(*turns)) == "", turns


@pytest.mark.parametrize("turns, expect", [
    (("Open Dashboard.", "Opened.", "Thanks.", "You're welcome. Would you like me to open it now?", "Yes."), "Open Dashboard."),
    (("Open Spotify.", "I couldn't find it running. Want me to launch it?", "Sure, go ahead."), "Open Spotify."),
    (("Open the Dashboard.", "I don't see it. Should I open the Dashboard page?", "Yes."), "Open the Dashboard."),
])
def test_the_plain_case_is_still_re_run(turns, expect):
    assert chatturn.yes_reissues(H(*turns)) == expect


@pytest.mark.parametrize("q", ["What's the status on the project we were working on yesterday?", "Give me an update on the project that was due Friday.",
                               "What's that program doing on my PC?", "What is that noise doing?", "What's the progress on the thing that was running?",
                               "what is that app up to", "Update on the Shadow World project we were building?",
                               "What's that thing doing, the fan is loud"])
def test_a_question_about_his_projects_is_not_a_question_about_jarvis(q):
    assert not chatturn._ACTIVITY_Q.search(q)
    assert "not doing anything between" not in chatturn.note(chatturn.CHAT, [{"role": "user", "content": q}]).casefold()


@pytest.mark.parametrize("q", ["What are you doing?", "What have you been up to?", "What were you working on?",
                               "What's the status on the thing that you were just working on?", "what are you up to"])
def test_a_question_about_what_jarvis_is_doing_is(q):
    assert chatturn._ACTIVITY_Q.search(q)


# ---- pcsteps after the review: only UI navigation, an app only when named, one ask -------------------------------------------
@pytest.mark.parametrize("said", [
    "Pick a number and pick a color", "Turn off the lights and turn on the fan.", "Enable dark mode and disable notifications",
    "check my battery and check the disk space", "disable the wifi and enable the hotspot", "turn on the lights and turn off the TV",
    "turn off the PC and enable the sleep timer", "Switch off the lights and turn on the fan", "check the box, click apply in Settings",
    "select all and press delete in Notepad", "Select the file and delete it in Explorer", "Press N, click Bridge On in Blender?",
    "Should I press N and click Bridge On in Blender?", "I pressed N and then I clicked Bridge On.", "please don't press N and click Bridge On in Blender",
    "do not click delete, click cancel in Chrome", "never press enter, click ok in Chrome", "if you see a popup click ok and press enter in Chrome",
    "the button says click here, click ok in Chrome", "I want to press N and click Bridge On in Blender", "He said to press N, click Bridge On in Blender.",
])
def test_ordinary_clauses_and_questions_are_not_ui_steps(said):
    assert pcsteps.split(said) == [], said


@pytest.mark.parametrize("said", ["Press N and click Bridge On.", "Open the Meshy panel in the Blender sidebar, press N, click Bridge On.",
                                  "press N and press M", "Open the settings menu, click Save in Steam", "In Blender press N then click Bridge On."])
def test_real_ui_steps_still_are(said, blender_only):
    assert pcsteps.split(said) or said.startswith("In Blender"), said


def test_the_app_is_not_guessed_from_the_conversation(blender_only, monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda name: {"sunny": "Sunshine", "joke": "Discord", "blender": "Blender"}.get(str(name).casefold()))
    for history in ([{"role": "user", "content": "It's sunny today."}, {"role": "assistant", "content": "Lovely, sir."}],
                    [{"role": "user", "content": "Tell me a joke."}, {"role": "assistant", "content": "Here is one about a joke."}]):
        found = pcsteps.plan_for("Press N and click Bridge On.", history + [{"role": "user", "content": "Press N and click Bridge On."}], "owner")
        assert "ask" in found, found
    pcsteps.reset()
    named = [{"role": "user", "content": "Okay. In Blender."}, {"role": "assistant", "content": "Which panel?"},
             {"role": "user", "content": "Press N and click Bridge On."}]
    assert pcsteps.plan_for("Press N and click Bridge On.", named, "owner")["app"] == "Blender"


def test_the_answer_must_follow_the_question_and_be_only_an_app(blender_only):
    question = pcsteps.plan_for("Press N and click Bridge On.", [], "owner")["ask"]
    asked = [{"role": "user", "content": "Press N and click Bridge On."}, {"role": "assistant", "content": question}]
    # the next line is something else: the held steps are gone for good
    assert pcsteps.answer_for("What time is it?", "owner", asked + [{"role": "user", "content": "What time is it?"}]) is None
    assert pcsteps.answer_for("In Blender.", "owner", asked + [{"role": "user", "content": "In Blender."}]) is None
    # a stale question: Jarvis said something else since
    pcsteps.plan_for("Press N and click Bridge On.", [], "owner")
    stale = asked + [{"role": "user", "content": "x"}, {"role": "assistant", "content": "Something else, sir."}, {"role": "user", "content": "Blender."}]
    assert pcsteps.answer_for("Blender.", "owner", stale) is None
    # and later lines never fire it
    for later in ("open notepad", "Notepad.", "Paint", "Steam"):
        pcsteps.plan_for("Press N and click Bridge On.", [], "owner")
        assert pcsteps.answer_for(later, "owner", asked + [{"role": "user", "content": "x"}, {"role": "assistant", "content": "ok"},
                                                          {"role": "user", "content": later}]) is None
    # the proper answer
    pcsteps.plan_for("Press N and click Bridge On.", [], "owner")
    ok = pcsteps.answer_for("In Blender.", "owner", asked + [{"role": "user", "content": "In Blender."}])
    assert ok and ok["app"] == "Blender" and ok["steps"][0]["arguments"]["goal"] == "press N; click Bridge On"


def test_the_answer_is_too_late_after_a_minute(blender_only, monkeypatch):
    import time as _t
    question = pcsteps.plan_for("Press N and click Bridge On.", [], "owner")["ask"]
    real = _t.monotonic()
    monkeypatch.setattr(pcsteps.time, "monotonic", lambda: real + 61)
    asked = [{"role": "user", "content": "Press N and click Bridge On."}, {"role": "assistant", "content": question},
             {"role": "user", "content": "In Blender."}]
    assert pcsteps.answer_for("In Blender.", "owner", asked) is None


def test_a_list_of_ui_steps_is_asked_about_once_before_anything_runs(blender_only, monkeypatch):
    from core import pchybrid
    decision = gateway._decide(OWNER_STEPS, [])
    intent = decision.intents[0]
    assert intent.kind == "actions" and intent.values.get("review"), "the plan is marked: ask once"
    seen = {}
    monkeypatch.setattr(pchybrid, "run", lambda said, cancelled=lambda: False, **kw: seen.update(kw) or {"state": "needs_review", "detail": "Should I?"})
    gateway.execute(decision, OWNER_STEPS)
    assert seen.get("review") is True


def test_the_review_asks_even_though_he_named_the_app(blender_only):
    """pchybrid.run with review=True puts the plan in the ledger and asks, whatever core.authz says about a named app."""
    from core import pchybrid, pcledger
    plan = {"steps": [{"tool": "use_app", "arguments": {"app": "Blender", "goal": "press N; click Bridge On"}}]}
    out = pchybrid.run("Press N and click Bridge On in Blender.", plan=plan, route="gateway", review=True)
    assert out["state"] == "needs_review" and "Bridge On" in out["detail"], out
    assert any(row for row in pcledger.pending()), "waiting for his yes in the ledger"


def test_without_the_review_flag_nothing_else_changed():
    from core import authz
    steps = [{"tool": "use_app", "arguments": {"app": "Blender", "goal": "click on Bridge On"}}]
    assert not authz.decide(steps, "Click Bridge On in Blender.").ask


# ================================ round-2 review 2026-11-15: correctness =====================================================
@pytest.fixture
def fuzzy_apps(monkeypatch):
    """The installed-app resolver is fuzzy (apps.resolve_app): the review's words that name another app by luck."""
    from tools import apps
    table = {"save": "Brave", "file": "Files", "edit": "Registry Editor", "first": "Files", "windows": "Windows PowerShell",
             "start": "Task Manager", "format": "Task Manager", "blender": "blender 5.2", "disk management": "Disk Management",
             "spotify": "Spotify", "notepad": "Notepad", "sunny": "Sunshine"}
    monkeypatch.setattr(apps, "resolve_app", lambda name: table.get(str(name).strip().casefold()))
    pcsteps.reset()
    yield
    pcsteps.reset()


@pytest.mark.parametrize("said", ["click the Save button and press enter", "click the File menu and click Exit", "click the Edit menu, click Paste",
                                  "click the first tab and click close", "click the Windows button and press enter",
                                  "click Format, click Start in Disk Management"])
def test_a_part_of_the_window_never_names_the_app(said, fuzzy_apps):
    found = pcsteps.plan_for(said, [], "u")
    app = (found or {}).get("app", "")
    assert app in ("", "Disk Management"), (said, found)
    if "Disk Management" in said:
        assert app == "Disk Management"          # an explicit 'in <app>' is the way to name one
    else:
        assert found and "ask" in found


def test_the_blender_sidebar_still_names_blender(fuzzy_apps):
    assert pcsteps.plan_for(OWNER_STEPS, [], "u")["app"] == "blender 5.2"
    assert pcsteps.plan_for("Press N and click Bridge On in Blender.", [], "u")["app"] == "blender 5.2"


def _H(*t):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": x} for i, x in enumerate(t)]


@pytest.mark.parametrize("history, expect", [
    (_H("Open Spotify.", "Opened.", "Thanks.", "You're welcome."), "ask"),                       # two lines back is not the previous line
    (_H("Okay. In Blender.", "Okay."), "blender 5.2"),                                            # the real call shape: no current line
    (_H("Okay. In Blender.", "Okay.", "Press N, click Bridge On."), "blender 5.2"),                # with the current line
    (_H("Open Spotify.", "Opened.", "Thanks.", "You're welcome.", "Press N, click Bridge On."), "ask"),
    (_H("Open Spotify.", "Opened."), "Spotify"),
])
def test_the_previous_line_is_read_whether_or_not_the_current_one_is_in_the_history(history, expect, fuzzy_apps):
    found = pcsteps.plan_for("Press N, click Bridge On.", history, "u")
    assert (found.get("app") if "steps" in found else "ask") == expect, found


@pytest.mark.parametrize("said", ["Press N and click Bridge On in Blender. Wait.", "Press N and click Bridge On in Blender. Cancel that.",
                                  "Press N and click Bridge On in Blender. Scratch that.", "Press N, click Bridge On in Blender. Just kidding.",
                                  "Press N, click Bridge On in Blender. Forget it.", "Press N, click Bridge On in Blender. Actually no.",
                                  "Press N, click Bridge On in Blender. Never mind."])
def test_a_retraction_after_a_full_stop_means_no_plan(said, fuzzy_apps):
    assert pcsteps.split(said) == [] and pcsteps.plan_for(said, [], "u") is None


# ---- the repeat guard only hands back a LOOP --------------------------------------------------------------------------------
def _flow(asked, earlier, d1, d2):
    said = []
    p = speech.Polisher(said.append, owner=True, title="sir", asked=asked, earlier=earlier, retry=True, fresh=lambda: False)
    for s in speech._SPLIT.split(d1):
        if s.strip():
            p(s)
    p.settle()
    if p.stale:
        p.once_more()
        for s in speech._SPLIT.split(d2):
            if s.strip():
                p(s)
        p.settle()
    p.finish()
    return said


ANSWER = "I am Jarvis, your personal assistant here to help with anything you need, sir."
THANKS = "You are welcome, sir, always happy to help with whatever you need."
WELL = "I am doing well today, sir, thank you for asking, and ready for whatever comes next."


@pytest.mark.parametrize("asked, earlier, answer", [
    ("What's your name?", [("Who are you?", ANSWER), ("What's the weather?", "Sunny and 72, sir."), ("ok", "Good.")], ANSWER),
    ("Tell me your name.", [("Who are you?", ANSWER), ("ok", "Good.")], ANSWER),
    ("I forgot your name.", [("Who are you?", ANSWER), ("ok", "Good.")], ANSWER),
    ("Cheers Jarvis.", [("Thanks.", THANKS), ("Hi", "Hello sir.")], THANKS),
    ("You good?", [("How are you?", WELL), ("ok", "Good.")], WELL),
])
def test_a_correct_answer_to_a_rephrased_line_is_said_as_written(asked, earlier, answer):
    assert " ".join(_flow(asked, earlier, answer, answer)) == answer


@pytest.mark.parametrize("reply", ["I lost my train of thought there, sir. Could you say it once more?",
                                   "I couldn't work out how to do that, sir, so I haven't touched anything. Give it to me one step at a time and I'll follow."])
def test_the_same_non_answer_twice_is_handed_back(reply):
    out = _flow("Okay. In Blender.", [("Open. Control. Center.", reply), ("Hi", "Hello sir.")], reply, reply)
    assert len(out) == 1 and "same answer" in out[0], out


def test_a_loop_that_asks_him_again_is_handed_back():
    out = _flow("Okay. In Blender.", [("Open. Control. Center.", STEP4), ("Hi", "Hello sir.")], STEP4, STEP4)
    assert len(out) == 1 and "same answer" in out[0], out


# ---- a web yes re-runs the search he asked for only when the offer is about it ---------------------------------------------
def test_a_web_yes_to_an_offer_about_something_else_runs_nothing():
    h = _H("Look up the weather in Paris.", "It's rainy. If you'd like, I can search for hotels in Paris too.", "Yes.")
    assert chatturn.yes_reissues(h, web=True) == ""
    h = _H("Search for flights to Paris.", "Found some. If you'd like, I can show you the cheapest.", "Yes.")
    assert chatturn.yes_reissues(h, web=True) == "Search for flights to Paris."
    h = _H("Look up the weather in Paris.", "It's rainy. If you'd like, I can show you the details.", "Yes.")
    assert chatturn.yes_reissues(h, web=True) == "Look up the weather in Paris."


@pytest.mark.parametrize("said", ["Yeah right.", "Yeah yeah.", "Sure sure.", "Okay okay.", "Oh yeah?"])
def test_a_dismissal_is_no_yes(said):
    assert not chatturn.bare_yes(said)
    assert chatturn.yes_reissues(_h(DASH, said)) == ""


def test_an_old_offer_is_not_re_run(monkeypatch):
    from core import pcfast
    import time as _t
    r = Replay(monkeypatch, {"Yes.": "Sir?"})
    seen = []
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: seen.append(text) and None)
    _seed(r, [("Open Dashboard.", "The Command Center is up on your screen, sir."),
              ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")])
    r.brain._last_turn = _t.monotonic() - 400                  # the offer is nearly seven minutes old
    r.say("Yes.")
    assert seen == ["Yes."], seen


def test_a_button_word_that_is_also_an_app_still_names_no_app(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda name: {"run": "Run", "blender": "blender 5.2"}.get(str(name).strip().casefold()))
    pcsteps.reset()
    found = pcsteps.plan_for("click the download button and click the run button", [], "u")
    assert found and "ask" in found, found


# ---- full gate 2026-11-15: two failures ------------------------------------------------------------------------------------------
def test_a_short_templated_answer_to_another_line_is_no_repeat():
    """tests/test_app.py: 'Answer to the typed one, sir.' after 'Answer to the spoken one, sir.' (a statement each time)."""
    said = []
    p = speech.Polisher(said.append, owner=True, title="sir", asked="a typed question about dinner",
                        earlier=[("a spoken question about the weekend", "Answer to the spoken one, sir.")], retry=True)
    p("Answer to the typed one, sir.")
    p.finish()
    assert said == ["Answer to the typed one, sir."] and not p.stale


def test_a_short_reply_word_for_word_to_a_statement_is_still_a_repeat():
    said = []
    p = speech.Polisher(said.append, owner=True, title="sir", asked="a typed question about dinner",
                        earlier=[("a spoken question about the weekend", "Answer to the spoken one, sir.")], retry=True)
    p("Answer to the spoken one, sir.")
    assert p.stale


def test_the_step_four_loop_is_still_caught_and_the_count_reads_right(blender_only):
    said = []
    p = speech.Polisher(said.append, owner=True, title="sir", asked="Okay. In Blender.",
                        earlier=[("Open. Control. Center.", STEP4)], retry=True)
    p(STEP4.split(". ")[0] + ".")
    assert p.stale
    assert pcsteps.plan_for("Press N and click Bridge On.", [], "u")["ask"].startswith("That's 2 steps, sir")
