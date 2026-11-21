"""Topic reset (live 2026-11-12): "Hey Jarvis." right after an independence report got more independence talk, and
small talk after a workout answer got workout talk. A line that is only a greeting is a hello wherever it comes (the
chat path, none of the last topic's skills), and a line that starts a new subject is answered on its own; a real
follow-up keeps its context. A fake transport stands in for the model: nothing here calls a real one."""
import pytest

from core import chatturn, intelligence, warmth
from tools import registry

MANUAL = "MANUAL-OPERATIONS " + "Follow the operating rules. " * 40
PERSONA = "PERSONA-CORE You are J.A.R.V.I.S., talking with them out loud."
PHONE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "

REPORT = [
    {"role": "user", "content": "How independent are you now?"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "independence_report", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "I have learned 3 skills in my lab. The paid teacher was called 2 times."}]},
    {"role": "assistant", "content": "I have learned 3 skills in my lab, sir. The paid teacher was called 2 times."},
]
SKY = [{"role": "user", "content": "Why is the sky blue?"},
       {"role": "assistant", "content": "Sunlight is scattered by the air, and blue light scatters the most, so the "
                                        "whole sky looks blue. At sunset the light crosses more air and turns red."}]


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL",
                "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    sent = []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        sent.append(body)
        return {"model": "qwen3.5:9b", "choices": [{"finish_reason": "stop", "message": {"content": "Hello, sir."}}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    return sent


def _last(body) -> str:
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)


@pytest.mark.parametrize("line", ["Hey Jarvis.", "Hello again.", "Good evening.", "Morning, Jarvis.", "Hey.", "Hi there",
                                  "Hello, sir!", "Bonjou Jarvis.", "Yo"])
def test_a_line_that_is_only_a_greeting(line):
    assert chatturn.greeting(line)


@pytest.mark.parametrize("line", ["Hey Jarvis, play some jazz", "Hello lights off", "hey what time is it", "Jarvis.",
                                  "Hi there, anything new?", "Good.", "Evening news", "Yes.", "Next one", ""])
def test_a_greeting_with_anything_more_is_not_one(line):
    assert not chatturn.greeting(line)


@pytest.mark.parametrize("prefix", ["", PHONE])
@pytest.mark.parametrize("line", ["Hey Jarvis.", "Hello again."])
def test_a_greeting_after_a_skill_is_a_hello_on_the_chat_path(local, line, prefix):
    """The live defect, on the typed path too (the phone app's channel note comes first)."""
    history = REPORT + [{"role": "user", "content": prefix + line}]
    assert not chatturn.command_like(line, history, [{"function": {"name": "independence_report"}}])
    assert intelligence.select_tools(history, registry.schemas()) == []
    intelligence.request("fast", MANUAL, history, registry.schemas(), chat_system=PERSONA)
    body = local[-1]
    assert body.get("profile") == chatturn.CHAT and body["tools"] == []
    assert body["messages"][0]["content"].startswith("PERSONA-CORE")
    assert chatturn.GREETING_NOTE in _last(body)


def test_the_first_hello_of_a_conversation_has_nothing_to_reset(local):
    history = [{"role": "user", "content": "Hey Jarvis."}]
    assert chatturn.note(chatturn.CHAT, history) == chatturn.BRIEF_NOTE
    assert not chatturn.new_subject("Hey Jarvis.", history)


@pytest.mark.parametrize("line", ["What is your favourite kind of music?", "I think I will make pancakes today.",
                                  "Tell me a joke.", "How does a microwave heat food?", "Hey Jarvis."])
def test_an_unrelated_line_is_a_new_subject(line):
    history = SKY + [{"role": "user", "content": line}]
    assert chatturn.new_subject(line, history)
    note = chatturn.note(chatturn.CHAT, history)
    assert chatturn.FRESH_NOTE in note or note == chatturn.GREETING_NOTE


@pytest.mark.parametrize("line", ["And why is that?", "So why are sunsets red then?", "Is the sunlight white?",
                                  "What about Mars?", "Huh, interesting.", "Not convinced.", "Could you go deeper?",
                                  "Does blue scatter more on a mountain?", "Why?", "Give me an example.",
                                  "Say that again in simpler words."])
def test_a_follow_up_keeps_its_context(line):
    history = SKY + [{"role": "user", "content": line}]
    assert not chatturn.new_subject(line, history)
    assert chatturn.FRESH_NOTE not in chatturn.note(chatturn.CHAT, history)
    assert chatturn.GREETING_NOTE not in chatturn.note(chatturn.DEPTH, history)


def test_a_new_subject_on_the_manuals_path_is_told_so_and_an_order_never_is(local, monkeypatch):
    monkeypatch.setattr(chatturn, "plan", lambda *a, **k: None)          # the manual's path, whatever the line
    asked = "Did you catch the football match yesterday evening?"
    intelligence.request("fast", MANUAL, SKY + [{"role": "user", "content": asked}], registry.schemas(), chat_system=PERSONA)
    assert chatturn.FRESH_NOTE in _last(local[-1])
    for line in ("Play some jazz in the kitchen please", "Open the downloads folder", "And why is that?"):
        local.clear()
        intelligence.request("fast", MANUAL, SKY + [{"role": "user", "content": line}], registry.schemas(), chat_system=PERSONA)
        assert all(chatturn.FRESH_NOTE not in _last(body) for body in local), line


def test_the_first_line_of_a_conversation_is_sent_as_it_always_was(local, monkeypatch):
    monkeypatch.setattr(chatturn, "plan", lambda *a, **k: None)
    intelligence.request("fast", MANUAL, [{"role": "user", "content": "Did you catch the football match yesterday?"}],
                         registry.schemas(), chat_system=PERSONA)
    assert _last(local[-1]) == "Did you catch the football match yesterday?"


# ---- review 2026-11-12: a pronoun follow-up and a Creole follow-up keep their context -----------------------------
PARIS = [{"role": "user", "content": "Tell me about Paris."},
         {"role": "assistant", "content": "Paris is the capital of France, known for its museums and cafes."}]
CURIE = [{"role": "user", "content": "Who was Marie Curie?"},
         {"role": "assistant", "content": "Marie Curie won two Nobel prizes."}]
PARI = [{"role": "user", "content": "Ki kapital Frans?"}, {"role": "assistant", "content": "Pari se kapital Frans."}]


@pytest.mark.parametrize("earlier, line", [
    (PARIS, "How many people live there?"), (CURIE, "How old was she when she died?"), (PARIS, "Is the food good?"),
    (CURIE, "Where did he study chemistry?"), (CURIE, "What was her husband called?"), (PARIS, "Who lives here nowadays?"),
    (CURIE, "Did his work matter much?"), (PARIS, "What do their trains cost?"), (PARIS, "And the weather?"),
    (PARI, "Kisa ou vle di?"), (PARI, "Konbyen moun ki rete la?"), (PARI, "Eske li bèl?"), (PARI, "Sa a enteresan anpil"),
    (PARI, "Poukisa yo renmen vil sa a?"), (PARIS, "Et pourquoi est-ce comme ça maintenant?"),
])
def test_a_pronoun_or_creole_follow_up_is_no_new_subject(local, monkeypatch, earlier, line):
    history = earlier + [{"role": "user", "content": line}]
    assert not chatturn.new_subject(line, history)
    assert chatturn.FRESH_NOTE not in chatturn.note(chatturn.CHAT, history)
    for prefix in ("", PHONE):                                           # the manual's path, spoken and typed
        monkeypatch.setattr(chatturn, "plan", lambda *a, **k: None)
        local.clear()
        intelligence.request("fast", MANUAL, earlier + [{"role": "user", "content": prefix + line}], registry.schemas(),
                             chat_system=PERSONA)
        assert all(chatturn.FRESH_NOTE not in _last(body) for body in local), line
