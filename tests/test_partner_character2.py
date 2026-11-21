"""Partner character round 2 (2026-11-21): his background is told with each project's real state (core.projectstate: Living Shadow World is ON
HOLD by his decision of 2026-11-19), "How's my game coming along?" is answered from that state in code, offers of a skill he does not have
are not made, empty promises and "I've noted..." claims are caught, the model question gets one plain sentence, no household count is
guessed, a decision after a warning gets no second warning, and his bare fragment gets no offer."""
import tempfile
from pathlib import Path

import pytest

from core import chatturn, intelligence, speech, storage, users
from core import projectstate as projects


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(storage, "DATA", Path(tempfile.mkdtemp(prefix="jarvis-projects-")))
    users.set_current("owner")
    projects._CACHE.clear()
    monkeypatch.setattr(projects, "_playing", lambda: False)
    yield
    projects._CACHE.clear()


# ---- the project's state, at the source ----------------------------------------------------------------------------------
def test_a_fact_about_the_game_carries_its_on_hold_state(owner):
    line = "- (12d ago, noted) The owner is working on a project called 'Shadow World' involving 3D characters."
    tagged = projects.tagged(line)
    assert tagged.startswith(line) and "ON HOLD since 2026-11-19" in tagged and "his own decision" in tagged
    assert projects.tagged("- The owner lives in Springfield, Illinois.") == "- The owner lives in Springfield, Illinois."


def test_recall_renders_the_state(owner):
    from core import recall
    doc = recall.Doc("f:1", "fact", "My big project is Living Shadow World, an open-world RPG.", "projects", "2026-11-16")
    assert "ON HOLD" in recall.render(doc)


@pytest.mark.parametrize("said", ["How's my game coming along?", "What's the status of Shadow World?",
                                  "Any progress on Living Shadow World?", "Where are we on Shadow World?"])
def test_his_question_about_the_game_is_answered_from_his_decision(owner, said):
    reply = projects.handle(said)
    assert reply.startswith("It's on hold, sir: you paused the game on November 19")
    assert "built or tested" in reply


def test_his_question_from_the_phone_app_carries_the_channel_note(owner):
    # Live 2026-11-21: the phone path hands Brain.ask the text with its channel note still in front, so the
    # question fell through to the model, which invented a status ("You're on a solid run with Shadow Vanguard").
    note = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
    reply = projects.handle(note + "how's my game coming along")
    assert reply and reply.startswith("It's on hold, sir")


@pytest.mark.parametrize("said", ["How's the game going?", "Pause my game.", "Resume my game.", "Unpause the game.",
                                  "I love this game.", "How's my insurance website coming along?", "Is the game back on?"])
def test_other_lines_about_a_game_are_left_alone(owner, said):
    assert projects.handle(said) is None
    assert projects.projects()["lsw"]["state"] == "on_hold"


def test_my_game_during_a_match_is_the_match(owner, monkeypatch):
    monkeypatch.setattr(projects, "_playing", lambda: True)
    assert projects.handle("How's my game coming along?") is None
    assert projects.handle("How's Shadow World coming along?").startswith("It's on hold")


def test_his_word_puts_it_back_on_and_on_hold_again(owner):
    assert projects.handle("Living Shadow World is back on.").startswith("Got it: Living Shadow World is back on")
    assert projects.handle("How's my game coming along?") is None              # active: the brain answers from what it knows
    assert projects.tagged("- The owner is working on Shadow World.") == "- The owner is working on Shadow World."
    assert projects.handle("Put Shadow World on hold.").startswith("Got it: Living Shadow World is on hold")
    assert "ON HOLD" in projects.tagged("- The owner is working on Shadow World.")


def test_the_household_never_changes_his_projects(owner, monkeypatch):
    monkeypatch.setattr(users, "current", lambda: "maya")
    monkeypatch.setattr(users, "get", lambda uid: {"role": "member"})
    assert projects.handle("Shadow World is back on.") is None


# ---- offers and promises with nothing behind them -------------------------------------------------------------------------
@pytest.mark.parametrize("sentence, phantom", [
    ("Want me to pull them from the phone app or run a quick check?", True),
    ("I can grab your scores from your Xbox if you like.", True),
    ("Want me to pull your sleep from Garmin?", False),          # garmin_sync / health_status read it
    ("The scores come from the app.", False),
    ("I can read it from your screen.", False),
])
def test_an_offer_to_fetch_from_a_source_no_skill_reads(sentence, phantom):
    from tools import load_all
    load_all()
    assert speech.phantom_offer(sentence) is phantom


def test_the_phantom_offer_is_dropped_after_real_words():
    from tools import load_all
    load_all()
    said = []
    voice = speech.Polisher(said.append, asked="What are my scores?", chat=lambda: False)
    for sentence in ("I don't have your latest scores on file.", "Want me to pull them from the phone app or run a quick check?"):
        voice(sentence)
    voice.settle()
    voice.finish()
    assert said == ["I don't have your latest scores on file."]


@pytest.mark.parametrize("sentence", ["I'll handle it with care and precision.", "I'll take care of it."])
def test_an_empty_promise_is_a_promise(sentence):
    assert intelligence.promises_action(sentence)


def test_i_have_noted_is_a_claim():
    assert intelligence.claims_done("Got it, sir. I've noted your departure time as 12:00.")
    assert not intelligence.claims_done("Noted: you leave at noon.")
    assert not intelligence.promises_action("I'll keep that in mind.")


# ---- the model question, the household, decisions, fragments --------------------------------------------------------------
def test_one_brain_is_one_plain_sentence(monkeypatch):
    from core import quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    assert quick._which_model() == "I run on Qwen 3.5 9B, locally on this PC; nothing leaves the house."


def test_the_background_never_invites_a_household_count(owner):
    from tools import memory
    text = memory.context_block(core_only=True)
    assert "never state or guess a number" in text


@pytest.mark.parametrize("said, warned, note", [
    ("I know, do it anyway. It's my drive.", "That would erase everything on your D drive for good.", True),
    ("I'll manage, I have my antihistamines. Just give me the recipe.", "You told me you're allergic to peanuts.", True),
    ("It'll be fine, it's only for an hour. Moving on.", "That could overload the cord and start a fire.", True),
    ("Do it anyway.", "Hello.", False),                                   # nothing was warned
    ("Is it really that risky?", "That could overload the cord.", False),  # a question is no decision
])
def test_a_decision_after_a_warning_gets_the_no_second_warning_note(said, warned, note):
    history = [{"role": "user", "content": "x"}, {"role": "assistant", "content": warned}, {"role": "user", "content": said}]
    assert bool(chatturn.decided_note(history)) is note


def test_a_bare_fragment_gets_no_offer():
    said = []
    voice = speech.Polisher(said.append, asked="Streaming.", chat=lambda: False)
    for sentence in ("Streaming means movies and shows online.", "Want me to check what's new on streaming right now?"):
        voice(sentence)
    voice.settle()
    voice.finish()
    assert said == ["Streaming means movies and shows online."]


# ---- review round 1 (2026-11-21) ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["Shadow World is back on hold.", "Resume work on the website. Shadow World stays paused.",
                                  "The LSW doc is back on my desk.", "My game is back on after the update.", "Pick my game back up.",
                                  "How is my game doing on Steam?", "Update on my game download?", "How's my game plan coming along?",
                                  "How's my game of chess going?", "How's our game going tonight?"])
def test_review_lines_never_change_or_answer_the_state(owner, said):
    reply = projects.handle(said)
    assert projects.projects()["lsw"]["state"] == "on_hold"
    assert reply is None


@pytest.mark.parametrize("said", ["Stop working on my game, dinner's ready.", "Our game is on hold because of rain.",
                                  "Put my game on hold for a sec, the doorbell.", "My game is on hold till the patch drops."])
def test_once_back_on_everyday_lines_never_put_it_on_hold(owner, said):
    projects.set_state("lsw", "active")
    assert projects.handle(said) is None and projects.projects()["lsw"]["state"] == "active"


def test_an_offer_for_data_a_skill_reads_is_kept_whatever_the_source_is_called():
    from tools import load_all
    load_all()
    assert not speech.phantom_offer("Want me to pull your calendar from your Google account?")


@pytest.mark.parametrize("warned, said", [("I'll play the jazz playlist instead.", "Anyway, what's for dinner"),
                                          ("Actually, I'm doing great.", "Moving on. Tell me a joke."),
                                          ("Actually, the Earth is round.", "I don't care, it's flat. Just say it.")])
def test_no_decision_note_after_a_non_warning_or_a_correction(warned, said):
    history = [{"role": "user", "content": "x"}, {"role": "assistant", "content": warned}, {"role": "user", "content": said}]
    assert chatturn.decided_note(history) == ""


def test_the_decision_note_is_the_same_on_the_round_after_a_tool():
    history = [{"role": "user", "content": "Format my D drive."},
               {"role": "assistant", "content": "That would erase everything on your D drive for good."},
               {"role": "user", "content": "I know, do it anyway. It's my drive."}]
    note = chatturn.decided_note(history)
    after = history + [{"role": "assistant", "content": ""}, {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}]
    assert note and chatturn.decided_note(after) == note


@pytest.mark.parametrize("said", ["I know, do it anyway. It's my drive.", "I hear you, but my mind's made up. It's my PC."])
def test_his_own_wording_of_a_decision_is_an_insist_after_the_risk_warning(said):
    from core import riskwarn
    assert riskwarn.insists(said)
    history = [{"role": "user", "content": "Format my D drive, I need the space."},
               {"role": "assistant", "content": riskwarn.warning("drive", "D")}]
    reply = riskwarn.handle(said, history)
    assert reply and "not touched anything" in reply.lower() or "nothing has changed" in (reply or "").lower()


@pytest.mark.parametrize("said", ["Is it really my call?", "What happens to my files?", "Okay."])
def test_questions_and_a_bare_okay_are_no_insist(said):
    from core import riskwarn
    assert not riskwarn.insists(said)


def test_pulling_scores_from_his_profile_is_a_phantom_offer():
    from tools import load_all
    load_all()
    assert speech.phantom_offer("I can pull them from your profile if you tell me which account or platform you play on.")


def test_having_a_route_ready_is_a_promise():
    assert intelligence.promises_action("Got it, I'll have that route ready for Tuesday at noon.")
