"""Everyday commands answered exactly, without the model: timers, focus, week, streak, countdowns."""
from datetime import date, timedelta

import pytest

from core import focus, hud, pcbridge, quick, timers
from tools import life, media, system


@pytest.fixture(autouse=True)
def pc(monkeypatch):
    monkeypatch.setattr(hud, "_send", lambda m: None)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(system, "announce", lambda t: None)
    monkeypatch.setattr(focus, "announce", lambda t: None)
    yield
    timers.clear()
    focus.stop()


@pytest.mark.parametrize("said, seconds, label", [
    ("Set a 3 minute timer for tea", 180, "tea"),
    ("set a pasta timer for 8 minutes", 480, "pasta"),
    ("Hey Jarvis, set a timer for 10 minutes", 600, "timer"),
    ("timer for an hour and 20 minutes for the roast", 4800, "roast"),
    ("start a 90 second timer", 90, "timer"),
    ("set a timer for half an hour", 1800, "timer"),
    ("Set a two minute plank timer", 120, "plank"),
    ("[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] set a 5 min timer for eggs", 300, "eggs"),
])
def test_setting_timers(said, seconds, label):
    reply = quick.handle(said)
    t = timers.active()
    assert t and t[0]["seconds"] == seconds and t[0]["label"] == label, (said, t)
    assert reply.endswith("I'll tell you when it's done.")


def test_list_and_cancel_timers_by_how_people_say_it():
    quick.handle("set a 3 minute timer for tea")
    quick.handle("set a 10 minute timer for laundry")
    assert quick.handle("what timers do I have running?").startswith("2 timers: tea, 3 minutes left; laundry, 10 minutes")
    assert quick.handle("how long is left on the tea timer") .startswith("2 timers")
    assert quick.handle("Cancel the tea timer") == "Cancelled the tea timer."
    assert quick.handle("cancel all timers") == "Cancelled the laundry timer."
    assert quick.handle("what timers are running") == "No timers are running."


def test_focus_by_voice():
    assert quick.handle("start focus mode for 50 minutes").startswith("Focus mode on: 4 blocks of 50 minutes")
    assert quick.handle("how long is left on my focus block").startswith("Focus:")
    assert quick.handle("stop focus mode").startswith("Focus mode off")


def test_week_streak_and_countdowns():
    assert quick.handle("How was my week?").startswith("Here's your week so far, since Monday")
    assert quick.handle("what's my streak").startswith("No workouts logged yet")
    life.add_countdown("Christmas", f"{date.today().year + 1}-12-25")
    assert "until Christmas, on" in quick.handle("How many days until Christmas?")
    assert quick.handle("how many days until the heat death of the universe") is None   # not a countdown: brain
    assert quick.handle("list my countdowns").startswith("Coming up: Christmas in")


@pytest.mark.parametrize("said", ["What time is it?", "Hey Jarvis, what's the time?", "what time is it now",
                                  "Do you have the time?", "What’s the time, please?"])
def test_the_time_is_instant(said):
    import re
    assert re.fullmatch(r"It's (1[0-2]|[1-9]):[0-5]\d (AM|PM)\.", quick.handle(said))


@pytest.mark.parametrize("said", ["What's the date?", "what day is it today", "What's today's date?", "what is today"])
def test_the_date_is_instant(said):
    from datetime import datetime
    now = datetime.now()
    assert quick.handle(said) == f"It's {now:%A, %B} {now.day}."


@pytest.mark.parametrize("said", [
    "what's the weather", "set the volume to 30", "I'm on a winning streak in Fortnite", "stop",
    "remind me to call mom at 5", "how was the movie", "open spotify", "tell me a joke",
    "the timer on my oven is broken, how do I fix it", "what time is the game tonight?",
    "what time is it in Tokyo?", "what day is Christmas this year?",
])
def test_everything_else_goes_to_the_brain(said):
    assert quick.handle(said) is None


def test_explicit_volume_changes_are_applied_as_relative_amounts(monkeypatch):
    state = {"volume": 100, "muted": False}
    monkeypatch.setattr(media, "state", lambda: dict(state))
    monkeypatch.setattr(media, "set_volume", lambda value: state.update(volume=value, muted=False))
    assert quick.handle("lower the volume by 50%") == "Volume is 50%."
    assert quick.handle("turn the volume up by 15 percent") == "Volume is 65%."
    assert quick.handle("set the volume to 30%") == "Volume is 30%."
    assert quick.handle("what is the volume?") == "Volume is 30%."


def test_off_the_pc_timers_are_left_to_the_brain(monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    assert quick.handle("set a 3 minute timer for tea") is None
    assert quick.handle("how was my week").startswith("Here's your week so far")   # data questions work anywhere


def test_duration_parsing():
    assert quick.duration("1 hour 30 minutes") == 5400
    assert quick.duration("forty-five seconds") == 45
    assert quick.duration("no numbers here") == 0
    assert (date.today() + timedelta(days=0)) == date.today()


def test_brain_speaks_a_direct_tools_result_without_a_second_model_pass(monkeypatch):
    from types import SimpleNamespace

    from anthropic.types import ToolUseBlock

    from brain import llm
    from core import intelligence
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    turns = []

    def one_turn(emit):
        turns.append(1)
        return SimpleNamespace(stop_reason="tool_use",
                               content=[ToolUseBlock(type="tool_use", id="c1", name="list_countdowns", input={})])
    monkeypatch.setattr(b, "_stream_turn", one_turn)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    said = []
    b.ask("what am I looking forward to", said.append)   # not a quick intent: the model picks the tool
    assert len(turns) == 1 and said and said[0].startswith("Nothing to count down to yet")
    assert b.messages[-1] == {"role": "assistant", "content": " ".join(said)}


# "What's 15 percent of 80?" went to the local model, which called engineering_calc('0.15 * 80', convert_to='percent')
# and announced "1200 percent". One right answer: it never reaches a model.
@pytest.mark.parametrize("said, answer", [
    ("What's 15 percent of 80?", "15 percent of 80 is 12."),
    ("Hey Jarvis, what is 20% of 1,250 dollars", "20 percent of 1,250 is 250."),
    ("how much is 7.5 percent of 200", "7.5 percent of 200 is 15."),
    ("30 percent off 60", "30 percent off 60 is 18 off, so 42."),
    ("[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] what's 50% of 9?", "50 percent of 9 is 4.5."),
])
def test_percent_of_a_number_is_instant(said, answer):
    assert quick.handle(said) == answer


@pytest.mark.parametrize("said", ["what is 15 percent of my budget", "what's 15 percent of 80 plus tax",
                                  "is 15 percent a good tip", "add 15 percent to the bill"])
def test_percent_questions_that_need_thought_go_to_the_brain(said):
    assert quick.handle(said) is None


def test_which_model_is_answered_from_the_configuration(monkeypatch):
    """"What local AI are you using?" got an invented "the PC's built-in AI and some custom scripts"."""
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.2:3b")
    monkeypatch.setenv("JARVIS_LOCAL_ADVANCED_MODEL", "llama3.1:8b")
    monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_VISION_MODEL", "qwen3-vl:8b-instruct")   # a separate eyes model is named on its own
    out = quick.handle("What local AI are you using?")
    assert out.startswith("I run on Llama 3.2 3B, with Llama 3.1 8B for harder questions")
    assert "Qwen 3 Vl 8B for pictures" in out and "nothing leaves the house" in out
    assert quick.handle("which model am I running right now") == out
    assert quick.handle("what model of car should I buy") is None
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "legacy")
    monkeypatch.delenv("JARVIS_LOCAL_ONLY")
    assert "cloud model" in quick.handle("What AI are you using?")


def test_one_brain_is_named_once(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")   # his .env; a checkout without it differs
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    out = quick.handle("What local AI are you using?")
    assert out == "I run on Qwen 3.5 9B, locally on this PC; nothing leaves the house."     # the owner's words, 2026-11-21


@pytest.fixture
def one_ollama_brain(monkeypatch):
    from core import intelligence, pcbridge, warmth
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(warmth, "jarvis_models", lambda: ["qwen3.5:9b"])
    return warmth


@pytest.mark.parametrize("said", ["is it running on my graphics card?", "Is the local AI running on the GPU?",
                                  "are you running on my GPU", "does it use the graphics card?", "is it on the CPU or the GPU?",
                                  "Jarvis, is it running on my graphics card right now?"])
def test_where_the_brain_runs_is_measured_not_guessed(one_ollama_brain, monkeypatch, said):
    """Live baseline 2026-11-10: 'is it running on my graphics card?' -> 'The local AI runs on your CPU, not your graphics
    card' while Ollama had all 6.6 GB of qwen3.5:9b in video memory."""
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: {"qwen3.5:9b": (6_607_507_290, 6_607_507_290)})
    assert quick.handle(said) == ("Qwen 3.5 9B is entirely on your graphics card: all 6.6 GB of it is in video memory, sir.")


def test_a_brain_split_between_gpu_and_cpu_or_not_loaded_is_said_as_measured(one_ollama_brain, monkeypatch):
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: {"qwen3.5:9b": (6_600_000_000, 4_100_000_000)})
    assert "only partly on your graphics card: 4.1 of its 6.6 GB" in quick.handle("is it running on my graphics card?")
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: {"qwen3.5:9b": (6_600_000_000, 0)})
    assert "running on the processor" in quick.handle("is it running on my graphics card?")
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: {})
    assert "isn't loaded at the moment" in quick.handle("is it running on my graphics card?")
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: None)
    assert "won't guess" in quick.handle("is it running on my graphics card?")


def test_the_laptop_never_reports_its_own_ollama_as_the_brain(one_ollama_brain, monkeypatch):
    """The laptop relays its thinking to the PC: its own Ollama says nothing about the brain he talks to."""
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: {"llama3.2:3b": (2_000_000_000, 0)})
    assert quick.handle("is it running on my graphics card?") is None


def test_a_game_on_the_graphics_card_is_not_the_brain(one_ollama_brain, monkeypatch):
    monkeypatch.setattr(one_ollama_brain, "loaded", lambda: {"qwen3.5:9b": (6_600_000_000, 6_600_000_000)})
    for said in ("is Apex running on my graphics card?", "is my graphics card hot?", "what's using my GPU?"):
        assert "Qwen" not in (quick.handle(said) or ""), said



def test_from_the_phone_the_pc_says_which_ai_answers(monkeypatch):
    """Live 2026-11-10 on the laptop: 'What local AI are you using?' -> 'Llama 3.2 3B ... Gemma 4 12b ...' (the laptop's own
    .env, which never answers a turn: its model calls are relayed to the PC), then 'Qwen 3.5 9B is entirely on your
    graphics card' for the next question. The PC says what it runs."""
    from core import pcfast
    asked = []
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: asked.append(raw) or "Locally I think with Qwen 3.5 9B for quick replies.")
    assert quick.handle("What local AI are you using?") == "Locally I think with Qwen 3.5 9B for quick replies."
    assert asked == ["What local AI are you using?"]
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: "I can't reach the PC right now, so I can't check that, and I won't guess. ")
    assert "won't guess" in quick.handle("which model are you running?")       # never the laptop's own models instead
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: None)
    assert quick.handle("What local AI are you using?") == quick.FROM_THE_PHONE


def test_a_household_member_on_the_phone_hears_the_truth_without_a_pc_question(monkeypatch):
    from core import pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)

    def not_owner(uid=None):
        raise pcbridge.PCError("PC control is available to the paired PC owner only.", 403)
    monkeypatch.setattr(pcbridge, "require_owner", not_owner)
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: pytest.fail("a member's question is never relayed to the PC"))
    out = quick.handle("What local AI are you using?")
    assert out == quick.FROM_THE_PHONE and "Llama" not in out


def test_from_the_phone_a_bare_answer_to_a_question_asked_here_is_understood_here(monkeypatch):
    """Laptop: 'Which one, sir: Steam or the Epic launcher?' then 'Steam.' The PC's probe has no history and calls a bare
    name 'a name, not a request' (wave 4): the answer went nowhere. This machine's gateway, which sees the question, takes it."""
    from core import gateway, pcfast
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    asked, decided = [], []
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: asked.append(raw) or None)
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None, ask=None: decided.append(raw) or None)
    question = [{"role": "user", "content": "open my game launcher"},
                {"role": "assistant", "content": "Which one, sir: Steam or the Epic launcher?"}]
    pcfast.handle("Steam.", question)
    assert asked == [] and decided == ["Steam."]
    pcfast.handle("Steam.", [])                           # no question waiting here: the PC judges it, as before
    pcfast.handle("open Steam", question)                 # a request: the PC's own gateway, as before
    assert asked == ["Steam.", "open Steam"]
