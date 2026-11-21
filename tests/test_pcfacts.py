"""The ONE deterministic question: "Is <app> open?" whole, about one exactly named app (core.pcfacts). Everything else, every polite
order and every other question, goes exactly where it went on main. Fakes only: no window, catalogue or model is read."""
import pytest

from core import gateway, pcfacts, pchybrid
from tools import apps

CATALOGUE = set(apps.BUILTINS) | {"steam", "google chrome", "microsoft edge", "discord", "visual studio code", "obs studio", "paint.net"}


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: set(CATALOGUE))
    monkeypatch.setattr(apps, "resolve_app", lambda name: name.casefold().strip() if name.casefold().strip() in CATALOGUE else None)


def fact(said):
    kind, values = pcfacts.classify(said)
    return values if kind == "fact" else None


ACCEPTED = [
    ("Is Notepad open?", "notepad", "open", ""), ("is notepad still open", "notepad", "open", ""), ("Is Notepad currently open?", "notepad", "open", ""),
    ("is notepad already open", "notepad", "open", ""), ("Is Notepad running?", "notepad", "open", ""), ("is notepad still running", "notepad", "open", ""),
    ("is calculator up", "calculator", "open", ""), ("Is Notepad closed?", "notepad", "closed", ""), ("is notepad still closed", "notepad", "closed", ""),
    ("is notepad shut", "notepad", "closed", ""), ("Is Steam open?", "steam", "open", ""), ("is spotify running", "spotify", "open", ""),
    ("Is Google Chrome open?", "google chrome", "open", ""), ("is microsoft edge still open", "microsoft edge", "open", ""),
    ("is visual studio code running", "visual studio code", "open", ""), ("Is Discord up?", "discord", "open", ""),
    ("is file explorer open", "file explorer", "open", ""), ("is task manager open", "task manager", "open", ""),
    ("is paint open or closed", "paint", "open", ""), ("is notepad open or not", "notepad", "open", ""),
    ("is notepad still running or closed", "notepad", "open", ""), ("is notepad still open or not", "notepad", "open", ""),
    ("do I have Notepad open", "notepad", "open", ""), ("do i still have steam running", "steam", "open", ""),
    ("Do I have Google Chrome open?", "google chrome", "open", ""), ("do I still have Paint open?", "paint", "open", ""),
    ("did you close Notepad?", "notepad", "closed", "did"), ("did you open notepad", "notepad", "open", "did"),
    ("Did you shut Steam", "steam", "closed", "did"), ("did notepad close", "notepad", "closed", "did"),
    ("Did Calculator open?", "calculator", "open", "did"), ("did google chrome close", "google chrome", "closed", "did"),
    ("Hey Jarvis, is Notepad open?", "notepad", "open", ""), ("Jarvis is notepad still open", "notepad", "open", ""),
    ("Jarvis, did you close Paint?", "paint", "closed", "did"), ("is notepad open please", "notepad", "open", ""),
    ("Is Notepad open, sir?", "notepad", "open", ""), ("is notepad open right now", "notepad", "open", ""),
    ("is notepad open at the moment", "notepad", "open", ""), ("Is Notepad still open right now, sir?", "notepad", "open", ""),
    ("hey jarvis is steam running please sir", "steam", "open", ""), ("IS NOTEPAD OPEN?!", "notepad", "open", ""),
    ("  is   notepad   open  ", "notepad", "open", ""), ("Is Paint.net open?", "paint.net", "open", ""),
    ("is obs studio running", "obs studio", "open", ""), ("do i have settings open", "settings", "open", ""),
    ("is camera still open", "camera", "open", ""), ("Is terminal open?", "terminal", "open", ""),
    ("is command prompt running", "command prompt", "open", ""),
]


@pytest.mark.parametrize("said, app, want, did", ACCEPTED)
def test_the_accepted_shapes_are_facts_about_the_named_app(said, app, want, did):
    assert fact(said) == {"kind": "app", "app": app, "want": want, "did": did}, said


NEAR_MISSES = [
    # the swallowed-order and hijacked-chat examples of the three review rounds
    "is notepad open close it", "is notepad open and close it", "is it open tomorrow", "is the dmv open", "is chrome better than edge",
    "have notepad open", "can you close notepad later", "what's on my screen", "is the sound on", "is the browser open",
    "is notepad++ open", "is teams open", "is notepad open, close it", "is notepad open then close it", "is notepad open if not open it",
    "is spotify open so play something", "is notepad open but minimised", "did you close notepad and open paint",
    "can you close notepad", "could you open notepad please", "would you mind closing notepad", "close notepad",
    # other clauses, commas, negations, a second verb, length
    "is notepad open, sir, and is paint", "is notepad not open", "isn't notepad open", "is notepad open? close it", "is notepad open. close it",
    "did you not close notepad", "didn't you close notepad", "did you close notepad or paint", "is notepad open or is paint open",
    "is notepad and paint open", "is notepad open and running", "is notepad still open or closed or running", "never is notepad open",
    "hey jarvis can you tell me if notepad is open right now", "is notepad open right now please sir at the moment", "do i have notepad and paint open",
    # other facts and questions: not this lane
    "what's open", "what's the volume", "is it muted", "is anything playing", "which window is in front", "is notepad in front",
    "is notepad minimized", "was notepad open earlier", "why did you close notepad", "how do i close notepad", "should i close notepad",
    "notepad's still open isn't it", "tell me if notepad is open", "check whether notepad is running",
    # not one exactly named app
    "is open", "is notepad", "is the notepad open", "is my notepad open", "is a notepad open", "is note open", "is notepa open",
    "is notepad window open", "is paint net open", "is chrome open", "is edge open", "is costco open", "is the door closed", "is it closed",
    "is unreal open", "is steam client open", "do i have the browser open", "did you open the browser", "is file open",
    "did you close it", "did you close", "is notepad open on my pc", "hello", "",
]


@pytest.mark.parametrize("said", NEAR_MISSES)
def test_a_near_miss_is_never_a_fact(said):
    assert fact(said) is None, said


def test_the_tables_are_big_enough():
    assert len(ACCEPTED) >= 45 and len(NEAR_MISSES) >= 60


def test_a_sentence_of_ten_words_is_not_a_fact():
    assert fact("is notepad open right now please sir at the moment") is None      # 10 words
    assert fact("Hey Jarvis, is Notepad still open right now, sir?") == {"kind": "app", "app": "notepad", "want": "open", "did": ""}   # 9


def test_the_name_must_be_the_catalogues_whole_name(monkeypatch):
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: {"notepad", "steam"})
    assert fact("is notepad++ open") is None and fact("is teams open") is None and fact("is steam open")
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: {"notepad", "notepad++"})
    assert fact("is notepad++ open")["app"] == "notepad++" and fact("is notepad open")["app"] == "notepad"


def test_a_name_two_entries_could_be_is_not_a_fact(monkeypatch):
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: {"paint.net", "paint-net", "notepad"})
    assert fact("is paint.net open") is None, "two catalogue spellings of one name: ambiguous"


def test_a_name_with_a_word_of_another_clause_is_not_one_app(monkeypatch):
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: {"rock and roll", "notepad"})
    assert fact("is rock and roll open") is None


# ---------------------------------------------------------------------------------------------------- the answer
@pytest.fixture
def pc(monkeypatch):
    box = type("Box", (), {"windows": []})()
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: box.windows)
    monkeypatch.setattr(apps, "game_processes", lambda app: [])
    monkeypatch.setattr(apps, "_process_stems", lambda: set())      # no real process list is read
    return box


def win(process, title, foreground=False, minimized=False):
    return {"hwnd": 1, "process": process, "title": title, "pid": 7, "foreground": foreground, "minimized": minimized}


def said(text):
    return pcfacts.answer(fact(text))


def test_the_answer_is_read_from_the_window_list_main_uses(pc):
    assert said("Is Notepad still open?") == "No, sir, I don't see a Notepad window."      # never "closed": nothing seen is not a state
    pc.windows = [win("Notepad.exe", "Untitled - Notepad")]
    assert said("Is Notepad still open?") == "Yes, sir, Notepad is open."
    pc.windows = [win("Notepad.exe", "Untitled - Notepad", foreground=True)]
    assert said("Is Notepad still open?") == "Yes, sir, Notepad is open. It's in front."
    pc.windows = [win("Notepad.exe", "Untitled - Notepad", minimized=True)]
    assert said("Is Notepad still open?") == "Yes, sir, Notepad is open."


def test_the_other_shapes_answer_from_the_same_state(pc):
    assert said("Did you close Notepad?") == "Yes, sir, I don't see a Notepad window any more."      # a clear yes about the state, no claim to remember closing it
    assert said("Did you open Notepad?") == "No, sir, I don't see a Notepad window."
    assert said("Is Notepad closed?") == "Yes, sir, I don't see a Notepad window any more."
    pc.windows = [win("Notepad.exe", "a - Notepad")]
    assert said("Did you close Notepad?") == "No, sir, Notepad is still open."
    assert said("Did you open Notepad?") == "Yes, sir, Notepad is open."
    assert said("Is Notepad closed?") == "No, sir, Notepad is still open."
    assert said("Do I have Notepad open?") == "Yes, sir, Notepad is open."


def test_the_desktop_is_not_an_open_app(pc):
    pc.windows = [win("explorer.exe", "Program Manager")]
    assert pcfacts.answer({"app": "files", "want": "open", "did": ""}) == "No, sir, I don't see a Files window."


def test_an_unreadable_window_list_is_said_not_guessed(pc, monkeypatch):
    def boom(app):
        raise OSError("no desktop")
    monkeypatch.setattr(pcfacts, "_windows_of_app", boom)
    assert said("Is Notepad open?") == "I can't read the window list just now, sir, so I won't guess whether Notepad is open."


# ------------------------------------------------------------------------------------------------ through the gateway
def no_model(*a, **k):
    raise AssertionError("a fact must not call a model")


def test_the_gateway_answers_the_fact_with_no_model_call_no_action_and_no_acknowledgement(pc, monkeypatch):
    ran = []
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: ran.append(a) or {"state": "completed", "detail": "did something"})
    decision = gateway.decide("Is Notepad still open?", ask=no_model)
    assert decision.kind == "fact" and decision.source == "exact" and decision.attempts == [] and decision.steps() == []
    assert gateway.execute(decision, "Is Notepad still open?") == "No, sir, I don't see a Notepad window."
    assert ran == []
    assert not gateway.wants_ack(decision) and gateway.ack_line(decision) == ""


def test_a_fact_on_the_laptop_is_not_read_from_the_laptops_windows(monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    hit = gateway.exact("Is Notepad still open?")
    assert hit is None or hit.kind != "fact"


def test_the_unreal_recogniser_keeps_its_sentence(monkeypatch):
    monkeypatch.setattr(gateway, "unreal_request", lambda said: {"steps": [{"tool": "unreal_status", "arguments": {}}]})
    assert gateway.exact("Is Notepad open?").kind == "actions"


def test_the_laptop_forwards_only_what_the_recogniser_accepts():
    from core import pcfast
    assert pcfast._asks_a_pc_fact("Is Notepad still open?") and pcfast._asks_a_pc_fact("did you close steam")
    for text in ("What's the volume?", "Which window is in front?", "is notepad open and close it", "Can you close Notepad?",
                 "Is the browser open?", "Close Notepad.", "Why is the sky blue?", "what's open"):
        assert not pcfast._asks_a_pc_fact(text), text


# ----------------------------------------------------------------- every other sentence goes where it went on main
# 40 polite orders and questions outside the narrow shapes. reading_request's expected values (FROZEN_READING) were produced by
# running MAIN's function (main 9907ce8) on each sentence; the branch must return the same. exact() depends on the catalogue, so
# for it the test shows the fact lane is the only difference: with the lane switched off the decision is the same, and it is
# never a "fact".
OUTSIDE = [
    "Can you close Notepad?", "Could you open Chrome please?", "Would you mind closing Paint?", "Will you minimise Edge for me?",
    "Can you bring Notepad to the front?", "Can you turn the volume down?", "Can you mute it?", "Could you close it?",
    "Is it possible to close Notepad?", "How about opening Notepad?", "Why don't you close Notepad?", "I want you to close Notepad.",
    "Can you tell me if Notepad is open?", "Could you check whether Notepad is running?", "Tell me if Notepad is open.",
    "What's open?", "What apps are open right now?", "What's the volume?", "Is it muted?", "Is anything playing?",
    "Which window is in front?", "Is Notepad in front?", "Is Notepad minimized?", "Is there a Notepad window open?",
    "Was Notepad open earlier?", "Why did you close Notepad?", "How do I close Notepad?", "Should I close Notepad?",
    "Close Notepad.", "Open Notepad", "Please close Notepad.", "Shut Notepad down for me.", "Close Notepad, will you?",
    "Is Notepad open, close it", "Is notepad open and close it", "Is the browser open?", "Is Notepad++ open?", "Is Teams open?",
    "Notepad's still open, isn't it?", "Is the sound on?",
]


def test_there_are_forty_sentences():
    assert len(OUTSIDE) >= 40 and len(set(OUTSIDE)) == len(OUTSIDE)


@pytest.mark.parametrize("said", OUTSIDE)
def test_the_fact_lane_changes_nothing_for_a_sentence_outside_the_shapes(said, monkeypatch):
    with_lane = gateway.exact(said)
    monkeypatch.setattr(pcfacts, "classify", lambda text, names=None: ("other", None))
    without = gateway.exact(said)
    shape = lambda d: None if d is None else [(i.kind, i.summary, i.steps, i.values, i.question) for i in d.intents]
    assert shape(with_lane) == shape(without)
    assert with_lane is None or with_lane.kind != "fact"


@pytest.mark.parametrize("said", OUTSIDE)
def test_reading_request_is_mains(said):
    assert gateway.reading_request(said) == FROZEN_READING[said], said


FROZEN_READING = {
    'Can you close Notepad?': False,
    'Could you open Chrome please?': False,
    'Would you mind closing Paint?': False,
    'Will you minimise Edge for me?': False,
    'Can you bring Notepad to the front?': False,
    'Can you turn the volume down?': False,
    'Can you mute it?': False,
    'Could you close it?': False,
    'Is it possible to close Notepad?': False,
    'How about opening Notepad?': False,
    "Why don't you close Notepad?": False,
    'I want you to close Notepad.': False,
    'Can you tell me if Notepad is open?': False,
    'Could you check whether Notepad is running?': True,
    'Tell me if Notepad is open.': False,
    "What's open?": False,
    'What apps are open right now?': False,
    "What's the volume?": True,
    'Is it muted?': False,
    'Is anything playing?': False,
    'Which window is in front?': True,
    'Is Notepad in front?': False,
    'Is Notepad minimized?': False,
    'Is there a Notepad window open?': False,
    'Was Notepad open earlier?': False,
    'Why did you close Notepad?': False,
    'How do I close Notepad?': False,
    'Should I close Notepad?': False,
    'Close Notepad.': False,
    'Open Notepad': False,
    'Please close Notepad.': False,
    'Shut Notepad down for me.': False,
    'Close Notepad, will you?': False,
    'Is Notepad open, close it': False,
    'Is notepad open and close it': False,
    'Is the browser open?': False,
    'Is Notepad++ open?': False,
    'Is Teams open?': False,
    "Notepad's still open, isn't it?": False,
    'Is the sound on?': False,
}
