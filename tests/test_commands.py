"""Commands the user teaches ("when I say blue notebook, reply only with noted") are kept on file and run exactly.

Live 2026-10-30: the teaching sentence ran no skill, the rule lived only in the chat, and after a restart the model
improvised ("Blue notebook is a notebook used by the owner", then a reminder). Triggers here are generated, not the
live phrase, so the fix is checked as a mechanism rather than a special case."""
import random
from difflib import SequenceMatcher

import pytest

from core import commands, quick, storage, verify
from tools import taught

WORDS_A = ["amber", "velvet", "copper", "quiet", "northern", "hollow", "paper", "crimson"]
WORDS_B = ["lantern", "harbour", "compass", "meadow", "anchor", "orchard", "falcon", "ledger"]
REPLIES = ["noted", "copy that", "all systems go", "the kettle is on", "acknowledged, sir", "standing by"]


def phrase(rng, unlike=()):
    """A random trigger; with `unlike`, one that no listed trigger is near (copper lantern / paper lantern are 0.88
    alike: after deleting one, the other would run for it, which is the STT tolerance doing its job, not a bug)."""
    while True:
        t = f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)}"
        if all(t != u and SequenceMatcher(None, _letters(t), _letters(u)).ratio() < commands.NEARLY for u in unlike):
            return t


@pytest.fixture
def rng():
    seed = random.randrange(1 << 30)
    print(f"seed={seed}")
    return random.Random(seed)


@pytest.mark.parametrize("template", [
    "When I say {t}, you need to reply with {r}.",
    "Hey Jarvis, when I say {t}, reply only with {r}",
    "If I say {t} say {r}.",
    "whenever I say '{t}', respond with \"{r}\"",
    "From now on when I say {t}, just say {r}",
    "Jarvis learn this: when I tell you {t}, answer only with {r}",
    "when I say {t} you say {r}",
    "every time I say {t}, say {r} please",
])
def test_teaching_sentences_store_the_rule_and_the_trigger_runs_it(template, rng):
    t, r = phrase(rng), rng.choice(REPLIES)
    said = quick.handle(template.format(t=t, r=r))
    assert said and said.startswith("Learned") and t in said and r in said
    rule = commands.find(t)
    assert rule and rule["response"] == r and rule["enabled"]
    for utterance in (t, t.title() + ".", f"hey jarvis, {t}!", f"Jarvis {t}?"):
        assert quick.handle(utterance) == r, utterance


def test_a_trigger_the_stt_heard_slightly_differently_still_runs():
    """Acceptance run, seed 11: Whisper heard the taught 'hollow pencil' as 'HoloPencil' the second time."""
    commands.teach("hollow pencil", "standing by")
    assert quick.handle("HoloPencil.") == "standing by"
    assert quick.handle("hollow pencils") == "standing by"
    assert quick.handle("hollow falcon") is None
    assert quick.handle("hollow") is None
    assert quick.handle("a hollow pencil please") is None


@pytest.mark.parametrize("a, b, near_a, near_b, ambiguous", [
    ("copper candle", "copper handle", "copper candel", "copper handel", "copper cand handle"),
    ("red kite", "red light", "red kites", "red lights", "red kight"),
    ("amber lantern", "amber lanterns", "Amber Lantern.", "amber lanterns!", "amber lanter"),
    ("silver mirror", "silver meadow", "silvermirror", "silver meadows", "silver mirrow"),
])
def test_similar_sounding_triggers_never_run_the_wrong_command(a, b, near_a, near_b, ambiguous):
    """The STT tolerance must not pick a neighbour: an exact trigger wins outright; a near miss runs only when one
    rule is near; when two rules are both near, neither runs (the brain hears it as ordinary speech)."""
    commands.teach(a, "response A")
    commands.teach(b, "response B")
    assert quick.handle(a) == "response A" and quick.handle(b) == "response B"
    assert quick.handle(near_a) in ("response A", None) and quick.handle(near_a) != "response B", near_a
    assert quick.handle(near_b) in ("response B", None) and quick.handle(near_b) != "response A", near_b
    assert quick.handle(ambiguous) is None or commands.find(ambiguous) is not None and \
        sum(SequenceMatcher(None, _letters(ambiguous), _letters(r["trigger"])).ratio() >= commands.NEARLY
            for r in commands.rules()) == 1, ambiguous


def _letters(text):
    return commands._norm(text).replace(" ", "").replace("-", "").replace("'", "")


def test_two_near_rules_run_neither():
    commands.teach("hollow pencil", "A")
    commands.teach("hollow pencils", "B")
    assert quick.handle("hollow pencil") == "A" and quick.handle("hollow pencils") == "B"
    assert quick.handle("HoloPencil") is None, "near both: ambiguous, so neither"
    commands.forget("hollow pencils")
    assert quick.handle("HoloPencil") == "A", "one rule near: it runs"


def test_rules_survive_a_restart(rng, monkeypatch):
    """A restart drops the conversation; the rule comes back from the file alone."""
    t, r = phrase(rng), rng.choice(REPLIES)
    quick.handle(f"when I say {t}, reply with {r}")
    rows = storage.load(commands.FILE, [])
    assert rows and rows[-1]["trigger"] == t and rows[-1]["response"] == r
    # a new process has no module state: the module is reloaded, the file is read fresh
    import importlib
    importlib.reload(commands)
    assert commands.handle(t) == r


def test_two_rules_live_side_by_side_and_updating_one_replaces_its_response(rng):
    t1 = phrase(rng)
    t2 = phrase(rng, unlike=(t1,))
    r1, r2, r1b = "noted", "copy that", "confirmed"
    quick.handle(f"when I say {t1} reply with {r1}")
    quick.handle(f"if I say {t2}, say {r2}")
    assert quick.handle(t1) == r1 and quick.handle(t2) == r2
    said = quick.handle(f"When I say {t1}, reply with {r1b} instead.")
    assert said.startswith("Updated") and r1b in said
    assert quick.handle(t1) == r1b, "the old response must be gone"
    assert quick.handle(t2) == r2, "the other rule is untouched"
    rule = commands.find(t1)
    assert [h["response"] for h in rule["history"]] == [r1]
    assert len(commands.rules()) == 2


@pytest.mark.parametrize("template", [
    "forget the {t} command", "delete the trigger for {t}", "remove the {t} rule", "unlearn the {t} command",
    "stop responding to {t}", "forget what you say when I say {t}",
])
def test_deleting_a_rule_stops_it_and_leaves_the_others(template, rng):
    t = phrase(rng)
    other = phrase(rng, unlike=(t,))
    quick.handle(f"when I say {t} say noted")
    quick.handle(f"when I say {other} say standing by")
    said = quick.handle(template.format(t=t))
    assert said and said.startswith("Forgotten") and t in said
    assert quick.handle(t) is None, "the brain takes the phrase again"
    assert quick.handle(other) == "standing by"
    assert commands.find(t) is None


def test_disabling_keeps_the_rule_but_stops_it(rng):
    t = phrase(rng)
    commands.teach(t, "noted")
    commands.forget(t, delete=False)
    assert quick.handle(t) is None
    assert commands.rules()[0]["enabled"] is False
    commands.teach(t, "noted")                      # teaching it again switches it back on
    assert quick.handle(t) == "noted"


def test_what_did_i_teach_you_is_answered_from_the_store(rng):
    assert "haven't taught me any" in quick.handle("what did I teach you?")
    t = phrase(rng)
    quick.handle(f"when I say {t} reply with noted")
    for q in ("what did I teach you?", "what commands have I taught you", "list my commands",
              f"what do you say when I say {t}"):
        said = quick.handle(q)
        assert said and t in said and "noted" in said and "reminder" not in said, q


@pytest.mark.parametrize("said", [
    "I bought a {t} yesterday", "the {t} is on the desk", "remind me about the {t} tomorrow",
    "remember that my {t} is blue", "what's a {t}?",
])
def test_a_sentence_that_merely_contains_the_trigger_is_not_hijacked(said, rng):
    t = phrase(rng)
    quick.handle(f"when I say {t} reply with noted")
    assert quick.handle(said.format(t=t)) is None


@pytest.mark.parametrize("said", [
    "forget the milk", "delete the reminder about the dentist", "remove eggs from my list",
    "stop replying to my emails", "when I say nothing you say nothing",
    "When I say go to bed, remind me to brush my teeth and lock the door and set the alarm for seven",
    "Remember that my sister Marie lives in Miami.", "Set a reminder for 5 pm to call mum", "what time is it",
])
def test_memory_reminders_and_everything_else_are_left_alone(said):
    assert commands.handle(said) is None
    assert commands.rules() == []


def test_teach_and_forget_as_skills_for_the_models_path(rng):
    t = phrase(rng)
    said = taught.learn_command(t, "all systems go")
    assert said.startswith("Learned") and quick.handle(t) == "all systems go"
    assert verify.check("learn_command", {"trigger": t, "response": "all systems go"}, said) is None
    assert verify.check("learn_command", {"trigger": t, "response": "something else"}, said)
    assert t in taught.list_commands()
    assert taught.forget_command(t).startswith("Forgotten") and quick.handle(t) is None
    assert "don't have a command" in taught.forget_command(t)


def test_learn_command_is_grounded_in_a_teaching_sentence():
    from core import intelligence
    g = intelligence._GROUNDING
    assert g["learn_command"].search("when I say amber lantern, reply with noted")
    assert not g["learn_command"].search("my amber lantern is in the garage")
    assert g["forget_command"].search("forget the amber lantern command")
    assert not g["forget_command"].search("what is the amber lantern command")
    assert intelligence._WRITES.match("learn_command") and intelligence._ACTS.match("learn_command")
    assert intelligence._ACTS.match("forget_command")


def test_the_store_is_bounded_and_tolerates_junk():
    storage.save(commands.FILE, [{"nonsense": 1}, "text", {"trigger": "x", "response": "y"}])
    assert [r["trigger"] for r in commands.rules()] == ["x"]
    for i in range(commands.MAX_RULES + 5):
        commands.teach(f"trigger {i}", "noted")
    assert len(commands.rules()) == commands.MAX_RULES
