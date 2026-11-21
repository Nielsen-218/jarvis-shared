"""Plain PC-fact questions run the School's proven playbooks in code; ordinary talk is never captured; loops are bounded."""
import json
from types import SimpleNamespace

import pytest

from core import intelligence
from tools import pcskills


def book(title, keywords, *, tier="T0", params=(), stale=False, kind="shell", subject="s"):
    return SimpleNamespace(title=title, keywords=sorted(keywords), tier=tier, params=list(params), stale=stale, kind=kind, subject=subject)


STORE = [
    book("how much free space is on my C drive", ["disk", "drive", "free", "much", "space", "storage"]),
    book("what is my IP address on this network", ["address", "ip", "ipv4", "network", "what"]),
    book("how much RAM does this computer have", ["computer", "does", "have", "memory", "much", "ram", "size"]),
    book("what Windows build number is this", ["build", "number", "os", "version", "what", "windows"]),
    book("how many logical processors does this PC have", ["cores", "cpu", "does", "have", "logical", "many", "pc", "processors", "threads"]),
    book("what is today's date", ["date", "today", "what"]),
    book("delete old logs", ["delete", "logs", "old", "files"], tier="T2"),
    book("how big is the folder", ["folder", "size", "big", "dir"], params=["dir"]),
    book("what is the old gpu name", ["gpu", "graphics", "card", "name"], stale=True),
]


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    monkeypatch.setattr(pcskills, "skills", lambda: SimpleNamespace(playbooks=SimpleNamespace(all=lambda: STORE)))


@pytest.mark.parametrize("question", [
    "How much free space is on my C drive?", "What's my IP address?", "How much RAM does this computer have?",
    "What Windows build am I on?", "How many CPU threads do I have?", "Hey Jarvis, how much free space is on my C drive",
])
def test_plain_pc_questions_find_their_proven_playbook(question):
    assert intelligence.pc_fact_playbook(question) is not None


def test_the_phone_apps_channel_note_does_not_hide_a_plain_pc_question():
    # Found live 2026-11-05: every question from the phone app starts with this note, which pushed it past the word limit,
    # so the 3B model answered from imagination ("Python 3.9", "243 GB free") instead of the proven playbook.
    note = "[Sent from their Jarvis phone app. Keep replies short and speakable; they may be read aloud.] "
    assert intelligence.pc_fact_playbook(note + "How much free space is on my C drive?") is not None
    assert intelligence.pc_fact_playbook(note + "What's the weather today?") is None


@pytest.mark.parametrize("sentence", [
    "What's the weather today?", "What did we learn today?", "How are you doing today?", "What's 15 percent of 80?",
    "Open calculator", "What is the best graphics card for gaming?", "Which computer should I buy?", "Why is my computer slow?",
    "How do I check my IP address?", "What should my RAM be for Blender?", "How much does a Windows license cost?",
    "Give me the exact website url publication date and explain whether you searched the live internet for this one today please",
    "Delete old logs", "How big is the folder",
])
def test_ordinary_talk_advice_writes_and_value_taking_skills_are_never_captured(sentence):
    assert intelligence.pc_fact_playbook(sentence) is None


def test_only_proven_read_only_non_stale_playbooks_without_values_can_match():
    for question, reason in (("what is the old gpu name of this graphics card", "stale"), ("delete old logs files", "tier T2"),
                             ("how big is the folder size", "needs a value")):
        assert intelligence.pc_fact_playbook(question) is None, reason


def test_utf16_output_from_wsl_is_spoken_as_plain_text():
    assert "Ubuntu" in pcskills._spoken("which linux is installed in wsl", "U\x00b\x00u\x00n\x00t\x00u\x00")


def test_output_is_phrased_as_one_short_sentence():
    assert pcskills._spoken("how much free space is on my C drive", "511274209280") == "476 GB free on your C drive."
    assert pcskills._spoken("how much RAM does this computer have", "33848745984") == "31.5 GB of memory."
    assert pcskills._spoken("what is my IP address on this network", "IPv4 Address. . . : 100.64.0.7(Preferred)\n IPv4 Address. . : 192.168.1.87(Preferred)") \
        == "Your IP addresses are 100.64.0.7, 192.168.1.87."
    assert pcskills._spoken("what time zone is this PC set to", "Eastern Standard Time") == "What time zone is this PC set to: Eastern Standard Time."
    assert pcskills._spoken("anything", "   ") is None
    assert len(pcskills._spoken("long", "x" * 5000)) < 300


def test_a_playbook_that_fails_verification_is_reported_honestly_not_guessed(monkeypatch):
    runs = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(result="FAIL"))
    monkeypatch.setattr(pcskills, "skills", lambda: SimpleNamespace(playbooks=runs, _guard=lambda: None, run_fn=None))
    said = pcskills.answer_with_playbook(STORE[0])
    assert "did not verify" in said and "won't guess" in said


def test_the_brain_speaks_the_playbook_result_itself_and_leaves_other_requests_alone(monkeypatch):
    from brain import llm
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    monkeypatch.setattr(pcskills, "answer_with_playbook", lambda found: "476 GB free on your C drive.")
    said = []
    b.messages = [{"role": "user", "content": "How much free space is on my C drive?"}]
    assert b._learned_skill_request("How much free space is on my C drive?", said.append) is True
    assert " ".join(said) == "476 GB free on your C drive." and b.messages[-1] == {"role": "assistant", "content": "476 GB free on your C drive."}
    assert b.messages[-3]["content"][0]["name"] == "pc_learned_skill"
    assert b._learned_skill_request("Tell me a joke", said.append) is False
    monkeypatch.setattr(pcskills, "answer_with_playbook", lambda found: (_ for _ in ()).throw(OSError("store unreadable")))
    assert b._learned_skill_request("How much free space is on my C drive?", said.append) is False   # a broken store falls back, never crashes


def test_the_same_tool_call_is_not_made_a_third_time_in_one_request(monkeypatch):
    from anthropic.types import TextBlock, ToolUseBlock
    from brain import llm
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b._local_role, b._cloud_calls, b._failed_calls = "fast", 0, set()
    ran = []
    monkeypatch.setattr(registry, "run", lambda name, args: (ran.append(name) or ("It is 5 pm.", False)))
    call = lambda n: SimpleNamespace(content=[ToolUseBlock(type="tool_use", id=f"c{n}", name="get_time", input={})], stop_reason="tool_use")
    turns = iter([call(1), call(2), call(3), call(4), SimpleNamespace(content=[TextBlock(type="text", text="never reached")], stop_reason="end_turn")])
    monkeypatch.setattr(b, "_stream_turn", lambda emit: next(turns))
    b.messages = [{"role": "user", "content": "What Windows build am I on?"}]
    said = []
    b._loop(said.append, 0)
    assert ran == ["get_time", "get_time"]            # two attempts, never a third
    assert "stopped instead of repeating" in " ".join(said)
    assert json.dumps(b.messages[-2]["content"][0]["is_error"]) == "true"
