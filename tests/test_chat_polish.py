"""Chat polish (2026-11-12, after a live check of chat first): a chat reply ends when it is said (no offer, no what-next
question); the background on him is the past, not the present moment; plain talk reaches the chat model with no planner
call and no manual before it, and the persona's prompt is warmed like the manual's. No real model, no real server."""
import json

import pytest

from core import chatturn, config, gateway, gateway_context, intelligence, speech, warmth
from tools import load_all, registry

load_all()


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)
    monkeypatch.setattr(chatturn, "_CHAT_REPLY", {})


# ------------------------------------------------------------------------------------------ 1. no closing offer

OFFERS = ["Want me to pull up today's workout or check how the character rig is coming along?",
          "Anything specific you want me to tackle next on the garden plan?",
          "Shall I look into it?", "Should I keep going?", "Would you like me to put the kettle on?",
          "Let me know if you need anything.", "Just say the word if you want company.", "What can I do for you?",
          "Is there anything you need from me tonight?", "I'm here if you need me.",
          "I can go through it with you if you like.", "If you need anything done on the PC, let me know.",
          "Just here if the kettle needs watching."]
NOT_OFFERS = ["Which part went wrong?", "How long has it been like that?", "Was it the manager again?",
              "Coffee before noon, tea after.", "I would let it rest a day.", "You could let me know how it went."]


@pytest.mark.parametrize("sentence", OFFERS)
def test_an_unrequested_offer_is_recognised(sentence):
    assert speech.chat_offer(sentence, "Long day.")


@pytest.mark.parametrize("sentence", NOT_OFFERS)
def test_a_question_about_what_he_said_or_an_answer_is_not_an_offer(sentence):
    assert not speech.chat_offer(sentence, "Long day.")


@pytest.mark.parametrize("asked", ["What can you do?", "Can you help me with this?", "What do you suggest?",
                                   "Any ideas?", "What should I do next?"])
def test_an_offer_stays_when_he_asked_what_jarvis_can_do(asked):
    assert not speech.chat_offer("Want me to draw up a plan?", asked)


def _said(sentences, asked, chat=True, keep=False):
    out = []
    voice = speech.Polisher(out.append, asked=asked, chat=lambda: chat, keep=lambda: keep)
    for s in sentences:
        voice(s)
    voice.settle()
    voice.finish()
    return out, voice.edits


@pytest.mark.parametrize("offer", OFFERS)
def test_a_chat_reply_ends_before_the_offer(offer):
    out, edits = _said(["That sounds like a long one.", offer], "Long day. Work was nonstop.")
    assert out == ["That sounds like a long one."]
    assert (offer, "") in edits                      # and the kept reply is what was said


def test_a_clarifying_question_at_the_end_is_said():
    out, _ = _said(["That sounds like a long one.", "Was it the manager again?"], "Long day. Work was nonstop.")
    assert out == ["That sounds like a long one.", "Was it the manager again?"]


def test_an_offer_in_the_middle_or_alone_is_said_never_dead_air():
    out, _ = _said(["Shall I look into it?", "It has been like that since Tuesday."], "It broke again.")
    assert out == ["Shall I look into it?", "It has been like that since Tuesday."]
    out, _ = _said(["Want me to keep you company?"], "Long day.")
    assert out == ["Want me to keep you company?"]


def test_he_asked_for_help_so_the_offer_is_the_answer():
    out, _ = _said(["A few things.", "Want me to draw up a plan?"], "What can you do about the plan?")   # (about his topic: core.speech.drags_in)
    assert out[-1] == "Want me to draw up a plan?"


def test_off_the_chat_path_a_step_to_ask_first_about_is_untouched():
    out, _ = _said(["Edge is not responding.", "Want me to restart it?"], "Edge froze again.", chat=False)
    assert out == ["Edge is not responding.", "Want me to restart it?"]


def test_a_question_really_waiting_is_kept():
    out, _ = _said(["Noted.", "Shall I go on?"], "Long day.", keep=True)
    assert out == ["Noted.", "Shall I go on?"]


# ------------------------------------------------------------------------------ 2. background is not the present

def test_the_background_is_presented_as_the_past_not_the_present():
    system = ("PERSONA\n\n<background>\nCurrent date and time: Thursday.\n" + intelligence._JOURNAL_HEAD
              + "\n- Tue: worked on the boat model.\n</background>")
    text = intelligence._as_known([{"role": "system", "content": system}, {"role": "user", "content": "Hey."}],
                                  "Can you remind me what we did on the boat model the other day?")[0]["content"]
    assert "None of it says what is happening now" in text and "unless they say so in this conversation" in text
    assert intelligence._KNOWN_TAIL in text and text.index(intelligence._KNOWN_TAIL) > text.index("boat model")
    assert "pick up open threads" not in text and "leave them be unless they bring one up" in text
    assert "<background>" not in text and text.endswith("</what-you-know>")


def test_the_persona_forbids_stating_what_is_happening_now_and_closing_offers():
    persona = config.load_persona_core(None, "owner")
    assert "never tells you what is happening now" in persona
    assert "You yourself have been doing nothing in the background" in persona
    assert "small talk ends when it is said" in persona
    for note in (chatturn.TALK_NOTE, chatturn.BRIEF_NOTE):
        assert "offer" in note


@pytest.mark.parametrize("sentence", ["My processors are idle while you work on your own projects.",
                                      "Just keeping an eye on the build for you.",
                                      "I've been monitoring the logs since you left.",
                                      "Keeping the systems quiet, as ever.",
                                      "You are still working on the boat model, I see."])
def test_a_plain_chat_sentence_that_invents_the_present_is_not_said(sentence):
    assert intelligence.invents_present(sentence, "How's it going?")
    assert intelligence.chat_unsaid(sentence, "How's it going?", acts=True)
    reply = {"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "Well enough. " + sentence}}]}
    kept = intelligence.parse_chat_reply(reply, "How's it going?", kind=chatturn.CHAT)
    assert " ".join(b.text for b in kept.content) == "Well enough."


def test_what_he_said_he_is_doing_may_be_said_back_and_an_explanation_is_not_judged():
    assert not intelligence.invents_present("Good luck while you work on it.", "I'm working on the boat model tonight.")
    assert not intelligence.invents_present("Waiting, mostly. It suits me.", "What are you up to?")
    assert not intelligence.chat_unsaid("The resolver answers while you wait for the page.", "How does DNS work?", acts=False)


def test_a_long_explanation_that_mentions_setting_is_not_an_action_just_done(local):
    long = ("At dusk the light crosses far more air, so the blue is scattered away and what reaches you is red. That is "
            "why the sun looks deep orange while it is setting, and paler again once it is higher in the sky at noon.")
    history = [{"role": "user", "content": "Why are sunsets red?"}, {"role": "assistant", "content": long},
               {"role": "user", "content": "Huh, interesting."}]
    assert not chatturn.command_like("Huh, interesting.", history)
    assert chatturn.command_like("Quieter", [{"role": "user", "content": "Play jazz."},
                                             {"role": "assistant", "content": "Playing jazz now, sir."},
                                             {"role": "user", "content": "Quieter"}])


# ----------------------------------------------------------------- 3. plain talk: no planner, no manual before it

def _plan(before, said, first="Morning."):
    history = [{"role": "user", "content": first}, {"role": "assistant", "content": before},
               {"role": "user", "content": said}]
    schemas = intelligence._schemas(intelligence.select_tools(history, registry.schemas()))
    return chatturn.plan("fast", history, schemas, False, None)


@pytest.mark.parametrize("said", ["Good to hear.", "Not really.", "Quiet evening, then.", "That's fair.", "I guess so.",
                                  "Pretty good, thanks.", "Makes sense."])
def test_a_short_remark_is_chat_whatever_skills_were_recalled(local, said):
    assert not chatturn.command_like(said, [{"role": "user", "content": "Morning."},
                                            {"role": "assistant", "content": "It rained most of the night."},
                                            {"role": "user", "content": said}], schemas=[{"name": "today_workout"}])
    assert _plan("It rained most of the night.", said) == chatturn.CHAT


@pytest.mark.parametrize("said", ["It's too loud", "That's frozen", "Jazz maybe"])
def test_a_short_complaint_or_request_with_a_skill_recalled_keeps_the_tools(local, said):
    history = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": "It rained most of the night."},
               {"role": "user", "content": said}]
    assert chatturn.command_like(said, history, schemas=[{"name": "set_volume"}])


@pytest.mark.parametrize("said", ["You're funnier than the last helper I had.",
                                  "Nothing is going right today and you keep missing the point.",
                                  "Just speak to me like a normal person for once."])
def test_a_word_about_when_or_whom_points_at_no_skill(local, said):
    assert not chatturn.pointed_at(said, [{"name": "undo_last_expense"}, {"name": "today_workout"},
                                          {"name": "switch_person"}])


def test_the_skills_own_word_still_points_at_it(local):
    assert chatturn.pointed_at("What's my workout for today?", [{"name": "today_workout"}])
    assert chatturn.pointed_at("Undo the last expense.", [{"name": "undo_last_expense"}])


def _plainly(before, said):
    history = [{"role": "user", "content": "Hey."}, {"role": "assistant", "content": before}]
    return gateway.plainly_talk(said, gateway_context.build(said, history), history)


def test_his_answer_to_a_question_from_a_chat_reply_needs_no_planner(local):
    before = "Well enough. How is your morning treating you?"
    assert not _plainly(before, "Pretty slow, honestly.")           # unknown where the question came from: the planner
    chatturn.note_reply("owner", before, True)
    assert chatturn.asked_in_chat([{"role": "assistant", "content": before}], "owner")
    assert _plainly(before, "Pretty slow, honestly.")
    assert _plainly(before, "What are you up to?")


def test_a_yes_a_clipped_command_or_the_machine_still_go_to_the_planner_after_a_chat_question(local):
    before = "Well enough. Shall I put some music on?"
    chatturn.note_reply("owner", before, True)
    for said in ("Yes.", "Sure, go ahead.", "Louder", "Open the browser then."):
        assert not _plainly(before, said), said


def test_a_question_from_any_other_reply_still_waits_for_the_planner(local):
    chatturn.note_reply("owner", "Well enough. How is your morning treating you?", True)
    assert not _plainly("I found two drives. Which one, sir?", "The bigger one.")
    chatturn.note_reply("owner", "Which one, sir?", False)          # a reply that was not chat forgets the mark
    assert not chatturn.asked_in_chat([{"role": "assistant", "content": "Which one, sir?"}], "owner")


# ------------------------------------------------------------------------------- 4. the persona prefix is warmed

class _Opener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request.full_url, json.loads(request.data)))

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return b'{"done": true}'
        return R()


@pytest.fixture
def ollama(local, monkeypatch):
    opener = _Opener()
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", opener))
    monkeypatch.setattr(intelligence, "_chat_warmed", {"prefix": "", "at": -1e9})
    return opener


def test_the_persona_prompt_is_read_ahead_in_a_chat_turns_own_layout(ollama):
    intelligence.prewarm_chat("PERSONA").join(5)
    (url, body), = ollama.requests
    assert url.endswith("/api/chat") and body["messages"] == [{"role": "system", "content": "PERSONA"}]
    assert not body.get("tools")                                     # a chat turn names no function at all
    assert body["options"]["num_ctx"] == 16384 and body["options"]["num_predict"] == 1   # same context: no reload
    assert body["keep_alive"] == "8h" and body["stream"] is False
    assert intelligence.prewarm_chat("PERSONA") is None              # warmed moments ago
    assert intelligence.prewarm_chat("") is None                     # no persona file: nothing to warm


def test_no_persona_warm_up_where_the_turns_go_to_the_pc(ollama, monkeypatch):
    from core import pcbridge
    monkeypatch.setenv("JARVIS_PC_URL", "http://pc.example:8765")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    assert intelligence.prewarm_chat("PERSONA") is None and not ollama.requests


def test_at_start_the_persona_is_warmed_and_the_gateway_context_built(monkeypatch):
    done = []
    monkeypatch.setattr(intelligence, "prewarm_chat", lambda system, wait=False: done.append(("chat", bool(system), wait)))
    monkeypatch.setattr(gateway_context, "build", lambda said, history: done.append(("context", said)))
    warmth.first_turn()
    assert done[0] == ("chat", True, True) and done[1][0] == "context"


def test_a_failed_warm_up_never_stops_the_keeper(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no server")
    monkeypatch.setattr(intelligence, "prewarm_chat", boom)
    monkeypatch.setattr(gateway_context, "build", boom)
    warmth.first_turn()
