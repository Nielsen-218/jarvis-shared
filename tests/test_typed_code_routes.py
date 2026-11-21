"""Lines decided in code before any model (his learned skills, independence report, study, language lesson, trainer,
whiteboard) are reached through POST /api/chat, whose text carries the phone app's channel note, as well as by voice.
Found live 2026-11-12: "What did you learn?" typed on the phone was answered by the chat model with small talk, because
learned.voice() anchors on the first word and saw "[Sent from their Jarvis phone app...]" first. No model runs here."""
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock
from fastapi.testclient import TestClient

from tools import registry


@pytest.fixture
def route(monkeypatch):
    from brain import llm
    from core import appserver, intelligence, pcfast, quick
    from tools import voice

    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, say=None, earcon=None: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    model_calls = []

    def request(role, system, history, tools, cancelled=None, on_text=None, **kw):
        model_calls.append(history[-1])
        return SimpleNamespace(content=[TextBlock(type="text", text="Small talk, sir.")], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    tools_run = []
    real_run = registry.run

    def run(name, args):
        tools_run.append(name)
        return real_run(name, args)
    monkeypatch.setattr(registry, "run", run)
    brain = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    brain.context = lambda: ""
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    client = TestClient(appserver.AppServer(lambda uid: brain).app)
    voice.pair_codes["333333"] = "owner"
    token = client.post("/api/pair", json={"code": "333333", "device": "Phone"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    def typed(line):
        model_calls.clear()
        tools_run.clear()
        reply = client.post("/api/chat", json={"text": line, "speak": False}, headers=headers).json()["reply"]
        return reply, list(tools_run), list(model_calls)

    def spoken(line):
        model_calls.clear()
        tools_run.clear()
        said = []
        brain.ask(line, said.append)
        return " ".join(said), list(tools_run), list(model_calls)
    return SimpleNamespace(typed=typed, spoken=spoken)


CASES = [
    ("What did you learn?", "learned_skills"),
    ("How independent are you?", "independence_report"),
    ("Undo that skill.", "remove_learned_skill"),
]


@pytest.mark.parametrize("line, tool", CASES)
@pytest.mark.parametrize("how", ["typed", "spoken"])
def test_his_own_records_are_answered_in_code_on_both_paths(route, line, tool, how):
    reply, ran, model = getattr(route, how)(line)
    assert tool in ran and not model, (how, ran, model)
    assert "Small talk" not in reply


@pytest.mark.parametrize("line", ["I want to be more independent.", "What did you learn about DNS?",
                                  "How independent is Scotland?"])
def test_ordinary_sentences_still_go_to_the_model(route, line):
    from core import learned
    assert learned.voice("[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] " + line) is None
    reply, ran, model = route.typed(line)
    assert not {"learned_skills", "independence_report", "remove_learned_skill"} & set(ran)


SIBLINGS = [
    ("Quiz me on chapter one.", "study"),
    ("How do you say water in Spanish?", "language"),
    ("Start a workout.", "trainer"),
    ("Show me DNS on the whiteboard.", "study_whiteboard"),
]


@pytest.mark.parametrize("line, kind", SIBLINGS)
def test_study_language_trainer_whiteboard_lines_are_taken_in_code_when_typed(route, monkeypatch, line, kind):
    taken = []
    from brain import llm
    for name in ("_language_request", "_study_request", "_trainer_request"):
        real = getattr(llm.Brain, name)

        def spy(self, text, emit, real=real, name=name):
            hit = real(self, text, emit)
            if hit:
                taken.append(name)
            return hit
        monkeypatch.setattr(llm.Brain, name, spy)
    reply, ran, model = route.typed(line)
    assert taken and not model, (taken, ran, model)


# ---- his Creole corrections (core/kreyol_bank): kept in code, typed or spoken ---------------------------------------
@pytest.mark.parametrize("how", ["typed", "spoken"])
def test_a_creole_correction_is_kept_in_code_on_both_paths(route, how):
    from audio import language
    from core import kreyol_bank
    language.reset()
    ask = getattr(route, how)
    reply, ran, model = ask("You should say mwen byen, not mwen bon.")
    assert not model and "mwen byen" in reply and "Small talk" not in reply
    assert [(r["right"], r["wrong"]) for r in kreyol_bank.kept("owner")] == [("mwen byen", "mwen bon")]
    # In a Creole conversation, after something Jarvis said: "they do not say that, they say ..."
    ask("Bonjou Jarvis, kijan ou ye jodi a? Mwen byen, mèsi.")
    reply, ran, model = ask("Yo pa di sa, yo di mwen grangou.")
    assert not model and reply.startswith("Mèsi.") and "mwen grangou" in reply
    assert kreyol_bank.count("owner") == 2
    language.reset()


@pytest.mark.parametrize("line", ["You should say sorry to her.", "You should say that again.",
                                  "They don't say much, they say it will rain."])
def test_ordinary_should_say_lines_still_go_to_the_model(route, line):
    from core import kreyol_bank
    reply, ran, model = route.typed(line)
    assert model and kreyol_bank.count("owner") == 0


# ---- the crash watchdog's record (core/watchdog): "did you crash" ----------------------------------------------------------
@pytest.mark.parametrize("how", ["typed", "spoken"])
@pytest.mark.parametrize("line", ["Did you crash?", "Why did you restart?", "Have you crashed recently?"])
def test_crash_questions_read_the_record_in_code(route, monkeypatch, tmp_path, line, how):
    from core import watchdog
    monkeypatch.setenv("JARVIS_WATCHDOG_DIR", str(tmp_path))
    watchdog.record_crash(1762997760, {"code": "0xc0000374", "name": "", "module": "core/inputwatch.py",
                                       "cause": "the controller watcher"}, True)
    reply, ran, model = getattr(route, how)(line)
    assert not model and not ran and "the controller watcher" in reply and "Small talk" not in reply


@pytest.mark.parametrize("line", ["Did the game crash?", "Why did my browser restart?", "Did you crash my game?",
                                  "I think the PC crashed", "Did you restart the router?"])
def test_ordinary_crash_sentences_still_go_to_the_model(route, monkeypatch, tmp_path, line):
    monkeypatch.setenv("JARVIS_WATCHDOG_DIR", str(tmp_path))
    from core import watchdog
    assert not watchdog.is_crash_question("[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] " + line)
    reply, ran, model = route.typed(line)
    assert model
