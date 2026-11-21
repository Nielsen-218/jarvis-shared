"""The running summary of a conversation's oldest exchanges (core/convsummary): both sides, grounded in what was said and
done, folded a batch at a time at background priority, and shown to the local model in their place (core/contextfit)."""
import json
import time
from types import SimpleNamespace

import pytest

from core import contextfit, convsummary, inferencegate, intelligence


def _turn(said, reply, call=None, result=None):
    group = [{"role": "user", "content": said}]
    if call:
        group += [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                      "name": call[0], "arguments": json.dumps(call[1])}}]},
                  {"role": "tool", "tool_call_id": "c1", "content": result}]
    return group + [{"role": "assistant", "content": reply}]


# The owner's own kind of evening: a list, a PC action, then talk about his son's bike and a decision.
GROUPS = [
    _turn("Add eggs and milk to my shopping list.", "Added eggs and milk to your shopping list, sir.",
          ("add_note", {"text": "eggs, milk", "list_name": "shopping"}), "Added to shopping (2 open items).\n- eggs\n- milk"),
    _turn("Close chat GPT.", "Closed ChatGPT."),
    _turn("I'm thinking about getting my son a new bike for Christmas.",
          "A bike he can grow into for a couple of years is usually the best value, sir."),
    _turn("Let's go with the 24-inch one, he's tall for his age.", "Very good, sir: the 24-inch frame it is."),
]


def _model(bullets, seen=None):
    def ask(prompt, cancelled):
        if seen is not None:
            seen.append(prompt)
        return list(bullets)
    return ask


def test_an_invented_bullet_is_dropped():
    out = convsummary.fold("", GROUPS, "Alex", ask=_model([
        "He asked to add eggs and milk to the shopping list; Jarvis added them.",
        "He wants a red Ferrari for his birthday next week.",                 # nobody said that
        "Alex prefers sushi restaurants downtown.",                        # nor that
    ]))
    assert "eggs and milk" in out
    assert "Ferrari" not in out and "sushi" not in out


def test_what_jarvis_did_and_what_he_decided_are_both_kept():
    seen = []
    out = convsummary.fold("", GROUPS, "Alex", ask=_model([
        "- He asked to add eggs and milk to the shopping list; Jarvis added them (2 open items).",
        "* Jarvis closed ChatGPT when asked.",
        "1. He is thinking about a new bike for his son for Christmas; he decided on the 24-inch one, as his son is tall.",
    ], seen))
    lines = out.splitlines()
    assert lines == ["- He asked to add eggs and milk to the shopping list; Jarvis added them (2 open items).",
                     "- Jarvis closed ChatGPT when asked.",
                     "- He is thinking about a new bike for his son for Christmas; he decided on the 24-inch one, as "
                     "his son is tall."]
    prompt = seen[0]
    # both sides go in, tool results as their first line only, never the notes Jarvis attaches
    assert "USER: Close chat GPT." in prompt and "JARVIS: Closed ChatGPT." in prompt
    assert "(Jarvis used add_note:" in prompt and "(result: Added to shopping (2 open items).)" in prompt
    assert "- eggs" not in prompt


def test_the_summary_is_bounded_and_the_oldest_notes_go_first():
    """At most ROOM tokens (the room the recap of his words had): over it, the oldest notes go, as in the recap."""
    previous = "\n".join(f"- He asked about the garden shelf number {i} and Jarvis answered about shelf number {i}."
                         for i in range(60))
    many = [f"He asked about the garden shelf number {i} and Jarvis answered about the garden shelf number {i}."
            for i in range(60, 80)]
    groups = [_turn(f"Tell me about the garden shelf number {i}.", f"The garden shelf number {i} is sturdy.")
              for i in range(60, 80)]
    out = convsummary.fold(previous, groups, "Alex", ask=_model(many))
    assert contextfit.tokens([{"content": out}]) <= convsummary.ROOM
    assert "shelf number 79" in out                             # the newest notes stay
    assert "shelf number 0 " not in out                         # the oldest went first


def test_without_a_model_the_notes_come_from_the_exchanges_themselves():
    def down(prompt, cancelled):
        raise intelligence.LocalFailure("local_request_failed")
    out = convsummary.fold("- They said: hello; Jarvis: Good evening.", GROUPS, "Alex", ask=down)
    assert "They said: Add eggs and milk to my shopping list.; Jarvis used add_note; Jarvis: Added eggs" in out
    assert "Jarvis: Closed ChatGPT." in out and "24-inch" in out
    assert "They said: hello; Jarvis: Good evening." in out          # the notes so far stay
    assert contextfit.tokens([{"content": out}]) <= convsummary.ROOM
    # every bullet invented: the same plain notes, never the inventions
    assert "Ferrari" not in convsummary.fold("", GROUPS, ask=_model(["He wants a red Ferrari for his birthday."]))


def test_a_turn_that_takes_the_card_first_means_not_now():
    assert convsummary.fold("", GROUPS, ask=lambda prompt, cancelled: None) is None


# -- a fold never loses what it folds (review, 2026-11-10) --------------------------------------------------------------
EARLIER = ("- He decided on the 24-inch bike for his son, who is tall for his age.\n"
           "- He asked to add eggs and milk to the shopping list; Jarvis added them.")
EVENING = [
    _turn("Book the dentist for Tuesday at 3 pm.", "The dentist is booked for Tuesday at 3 pm, sir.",
          ("set_reminder", {"text": "dentist", "when": "Tuesday 15:00"}), "Reminder set for Tuesday 15:00."),
    _turn("Text Maya that I'm running late.", "Sent Maya a text saying you're running late, sir.",
          ("send_message", {"to": "Maya", "text": "Running late"}), "Message sent to Maya."),
    _turn("Set the thermostat to 70.", "The thermostat is set to 70 degrees, sir.",
          ("set_thermostat", {"degrees": 70}), "Thermostat set to 70."),
    _turn("What was the Commanders score?", "The Commanders won 27 to 20, sir."),
    _turn("Remind me to take the trash out at 8.", "I'll remind you about the trash at 8 pm, sir.",
          ("set_reminder", {"text": "trash", "when": "20:00"}), "Reminder set for 20:00."),
    _turn("How long is the drive to Norfolk?", "About 25 minutes to Norfolk right now, sir."),
]


def _down(prompt, cancelled):
    raise intelligence.LocalFailure("local_request_failed")


def test_without_a_model_the_earlier_notes_and_every_exchange_of_the_batch_stay():
    """The fallback kept ~120 words, the newest plain lines: the bike decision, the eggs and the dentist booking were
    all gone while upto moved past them."""
    out = convsummary.fold(EARLIER, EVENING, "Alex", ask=_down)
    for kept in ("24-inch bike", "eggs and milk", "dentist", "Maya", "thermostat", "Commanders", "trash", "Norfolk"):
        assert kept in out, kept


def test_the_notes_on_the_batch_just_folded_are_never_the_ones_cut():
    """The model restated 7 earlier notes (112 words) and added the one for this batch; the cap kept the oldest 7."""
    earlier = [f"He talked about garden project number {i} with Jarvis and decided to build it next spring." for i in range(7)]
    previous = "\n".join(f"- {b}" for b in earlier)
    dentist = "He asked Jarvis to book the dentist for Tuesday at 3 pm; Jarvis set the reminder for Tuesday 15:00."
    out = convsummary.fold(previous, EVENING[:1], "Alex", ask=_model(earlier + [dentist]))
    assert dentist in out
    assert all(b in out for b in earlier)                       # ...and nothing earlier went for it
    assert out.count("garden project number 3") == 1            # a note restated is not kept twice


def test_a_model_that_writes_only_the_new_part_never_wipes_the_earlier_notes():
    out = convsummary.fold(EARLIER, EVENING[:2], "Alex", ask=_model([
        "He asked Jarvis to book the dentist for Tuesday at 3 pm; Jarvis set the reminder.",
        "Jarvis sent Maya a text saying he is running late."]))
    assert "24-inch bike" in out and "eggs and milk" in out     # what was decided at the start
    assert "dentist" in out and "Maya" in out


def test_an_exchange_cut_from_the_model_s_transcript_still_gets_its_note():
    """The transcript was cut to its last 12,000 characters, but upto still covered the first exchanges."""
    long = _turn("Read me the whole terms of service of the router, the long version please.",
                 "The router's terms say " + "the vendor may update the firmware at any time and " * 400)
    seen = []
    out = convsummary.fold("", [long] + EVENING[:2], "Alex", ask=_model([
        "He asked Jarvis to book the dentist for Tuesday at 3 pm; Jarvis set the reminder.",
        "Jarvis sent Maya a text saying he is running late."], seen))
    assert "router" in seen[0]                                  # every exchange reaches the model, shortened
    assert "router" in out and "dentist" in out and "Maya" in out


def test_a_preference_said_in_other_words_keeps_its_exchange():
    """'He wants shorter replies from now on.' shares only 'short' with 'Keep it short from now on, okay?': it is
    dropped as a guess, and the exchange must not leave the conversation with it."""
    groups = [_turn("Keep it short from now on, okay?", "Understood, sir."), *EVENING[:1]]
    out = convsummary.fold("", groups, "Alex", ask=_model([
        "He wants shorter replies from now on.",
        "He asked Jarvis to book the dentist for Tuesday at 3 pm; Jarvis set the reminder."]))
    assert "Keep it short from now on" in out and "dentist" in out


def test_a_note_never_turns_a_failed_action_into_a_done_one():
    """'Jarvis set a reminder' after the skill answered 'Couldn't read that time.' passed the two-word check (his own
    request counts as source) and later read as the record of what Jarvis did: 'Yes sir, it's set for noon.'"""
    groups = [_turn("Remind me to call the dentist at noon.", "I couldn't read that time, sir.",
                    ("set_reminder", {"text": "call the dentist", "when": "noon"}), "Couldn't read that time."),
              _turn("Add milk to the shopping list.", "The notes file is locked, sir; nothing was added.",
                    ("add_note", {"text": "milk", "list_name": "shopping"}), "Error: the notes file is locked."),
              _turn("Delete the old backups.", "Shall I delete the old backups in D:/Backups, sir?",
                    ("delete_files", {"path": "D:/Backups"}), "NEEDS_CONFIRMATION: delete D:/Backups. Ask the user.")]
    out = convsummary.fold("", groups, "Alex", ask=_model([
        "Jarvis set a reminder to call the dentist at noon.",
        "Jarvis added milk to the shopping list.",
        "Jarvis deleted the old backups.",
        "Jarvis confirmed the dentist reminder for noon.",
        "Jarvis fixed the errors and added milk to the shopping list.",
        "He asked for a dentist reminder at noon, but Jarvis could not read that time."]))
    assert "Jarvis set a reminder" not in out and "Jarvis added milk" not in out and "deleted the old backups" not in out
    assert "confirmed the dentist" not in out and "fixed the errors" not in out
    assert "could not read that time" in out                    # the faithful note stays
    assert "add_note" in out and "failed" in out                # the plain note says it failed
    assert "not done" in out                                    # ...and that the delete only asked first


def test_a_progress_line_is_not_what_jarvis_answered():
    group = _turn("Fix the planner for me.", "Give me a second, sir, I'm working on it. Sir, it failed again. "
                                             "The same planner timeout came back.")
    out = convsummary.fold("", [group], "Alex", ask=_down)
    assert "it failed again" in out and "working on it" not in out


def test_an_offer_he_did_not_take_up_is_not_an_open_item():
    seen = []
    convsummary.fold("", GROUPS, "Alex", ask=_model(["He asked to add eggs and milk to the shopping list."], seen))
    assert "did not take up is not open" in seen[0]


# -- the Brain's side ---------------------------------------------------------------------------------------------------
SYSTEM = "Jarvis instructions. " * 1600          # ~7.7k tokens, like personality.md


def _brain(monkeypatch, exchanges=30):
    from brain import llm
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", SYSTEM, uid="owner")
    b._last_turn = time.monotonic()              # a conversation going on now (not one to journal)
    for i in range(exchanges):
        b.messages += [{"role": "user", "content": f"Question {i} about the garden shelves?"},
                       {"role": "assistant", "content": f"Answer {i} about the garden shelves. " + "words " * 90}]
    return b


def test_a_long_conversation_folds_its_oldest_batch_into_the_summary(monkeypatch):
    b = _brain(monkeypatch)
    local = intelligence.messages_for_local(SYSTEM, b.messages)
    assert contextfit.dropped(local) > 0                         # the next turn would lose exchanges
    seen = []
    assert convsummary.update(b, ask=_model(["He asked about the garden shelves; Jarvis answered each question."], seen))
    assert b._summary == "- He asked about the garden shelves; Jarvis answered each question."
    assert b._summarized_upto % convsummary.BATCH == 0 and b._summarized_upto >= convsummary.BATCH
    assert "Question 0 about the garden shelves?" in seen[0] and "Question 6 about" not in seen[0]
    assert contextfit.dropped(intelligence.messages_for_local(SYSTEM, b.messages), b._summary, b._summarized_upto) == 0
    assert not convsummary.update(b, ask=_model(["anything"]))   # nothing more to fold: no model call
    from core import journal
    monkeypatch.setattr(journal, "archive_local", lambda uid, old, **kw: None)
    b._archive()                                                 # a new conversation starts without it
    assert (b._summary, b._summarized_upto) == ("", 0)


def test_a_short_conversation_needs_no_summary(monkeypatch):
    b = _brain(monkeypatch, exchanges=3)
    assert not convsummary.update(b, ask=lambda *a: pytest.fail("no model call for a conversation that fits"))
    assert b._summary == ""


def test_nothing_is_summarized_while_he_is_gaming(monkeypatch):
    b = _brain(monkeypatch)
    monkeypatch.setattr(inferencegate, "GAMING", lambda: True)
    monkeypatch.setattr(inferencegate, "POLL_S", 0.05)
    assert not convsummary.update(b, ask=lambda *a: pytest.fail("no model call during a game"), wait=0.3)
    assert b._summary == "" and b._summarized_upto == 0
    monkeypatch.setattr(inferencegate, "GAMING", lambda: False)  # the game ended: the next turn's fold runs
    assert convsummary.update(b, ask=_model(["He asked about the garden shelves; Jarvis answered each question."]))


def test_a_summary_of_an_archived_conversation_never_lands_on_the_next(monkeypatch):
    b = _brain(monkeypatch)
    from core import journal
    monkeypatch.setattr(journal, "archive_local", lambda uid, old, **kw: None)

    def archived_meanwhile(prompt, cancelled):
        b._archive()
        b.messages = [{"role": "user", "content": "Good morning."}, {"role": "assistant", "content": "Morning, sir."}]
        return ["He asked about the garden shelves; Jarvis answered each question."]
    assert not convsummary.update(b, ask=archived_meanwhile)
    assert b._summary == "" and b._summarized_upto == 0


def test_the_next_local_request_carries_the_summary_in_place_of_the_folded_exchanges(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")   # the manual's path; a chat turn shows the exchanges whole while they fit
    """System + the summary block + the exchanges kept word for word + his new question, as Ollama receives it."""
    from brain import llm
    from core import pcfast, quick
    b = _brain(monkeypatch)
    convsummary.update(b, ask=_model(["He asked about the garden shelves; Jarvis answered each question."]))
    upto = b._summarized_upto
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(convsummary, "after_turn", lambda brain: None)
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    sent = []

    def complete_local(payload, cancelled=lambda: False, on_text=None):
        sent.append(payload)
        return {"model": "qwen3.5:9b", "choices": [{"message": {"role": "assistant", "content": "The oak one, sir."},
                                                    "finish_reason": "stop"}], "usage": {}}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    said = []
    b.ask("And which one should I buy?", said.append)
    assert said and "oak" in said[-1]
    messages = sent[0]["messages"]
    texts = [contextfit._text(m) for m in messages]
    assert messages[0]["role"] == "system" and texts[1].startswith(contextfit.SUMMARY_HEAD)
    assert "Jarvis answered each question." in texts[1]
    assert not any(f"Question {i} about" in t for i in range(upto) for t in texts)        # folded
    assert f"Question {upto} about the garden shelves?" in "".join(texts[1:3])             # kept word for word
    assert texts[-1].startswith("And which one should I buy?")                            # his new question last


def test_the_summary_runs_only_where_the_model_is_this_machines(monkeypatch):
    from core import pcbridge
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    assert convsummary.enabled()
    monkeypatch.setenv("JARVIS_CONV_SUMMARY", "off")
    assert not convsummary.enabled()
    monkeypatch.delenv("JARVIS_CONV_SUMMARY")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)      # the laptop: its turns go to the PC's model
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example:8766")
    assert not convsummary.enabled()


def test_the_model_call_gives_way_to_his_turn(monkeypatch):
    """A turn that took the card first: the notes' call gives up at once (background inference) and waits for a gap."""
    def busy(*a, **k):
        raise intelligence.LocalFailure("local_model_busy")
    monkeypatch.setattr(intelligence, "request", busy)
    assert convsummary._ask("prompt", lambda: False) is None
    assert convsummary._ask("prompt", lambda: True) is None      # his turn began: no call at all

    def answers(role, system, history, tools, cancelled=lambda: False, **kw):
        assert role == "fast" and tools == [] and kw["response_format"]["type"] == "json_schema"
        from anthropic.types import TextBlock
        return SimpleNamespace(content=[TextBlock(type="text", text='{"bullets": ["He asked about the garden."]}')])
    monkeypatch.setattr(intelligence, "request", answers)
    assert convsummary._ask("prompt", lambda: False) == ["He asked about the garden."]
