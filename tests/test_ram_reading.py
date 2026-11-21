"""'How much RAM is free?' was "I couldn't work out how to do ..." (live 2026-11-15, by voice and by chat): the system-memory reading did not
exist (CPU, GPU, temperature, uptime and video memory did)."""
import pytest

from core import quick
from tools import registry

STATUS = {"cpu_percent": 22.0, "gpu_load_percent": 6, "memory_used_gb": 22.4, "memory_total_gb": 31.5, "disk_free_gb": 410.2,
          "vram_free_gb": 8.1, "vram_total_gb": 12.0, "uptime_hours": 30.0}


@pytest.fixture(autouse=True)
def _status(monkeypatch):
    monkeypatch.setattr(registry, "run", lambda name, args: (dict(STATUS), False) if name == "get_system_status" else ("", True))


@pytest.mark.parametrize("said", ["How much RAM is free?", "How much memory is free right now?", "What's my memory usage?",
                                  "How much RAM is left?", "What is the RAM usage", "How much memory is free on my PC, please?",
                                  "Hey Jarvis, how much RAM is available?"])
def test_a_memory_question_is_answered_from_the_status(said):
    reply = quick.handle(said)
    assert reply and "9.1 GB of memory is free" in reply and "22.4 of 31.5 GB in use" in reply, reply


@pytest.mark.parametrize("said", ["How much RAM is the game using?", "How much memory is Chrome using?", "Scan my PC and look for things that are wasting my memory.",
                                  "I need more memory for this", "Remember my memory of last year.", "How much memory does Blender need?"])
def test_other_memory_words_are_not_the_reading(said):
    reply = quick.handle(said) or ""
    assert "of memory is free" not in reply, reply


def test_video_memory_keeps_its_own_answer():
    reply = quick.handle("How much video memory is free?")
    assert reply and "GB of video memory is free" in reply and "of memory is free, with" not in reply


def test_the_cpu_answer_is_unchanged():
    assert "percent load" in quick.handle("What is my CPU usage?")


def test_no_reading_says_so_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(registry, "run", lambda name, args: ("", True))
    assert "couldn't read" in quick.handle("How much RAM is free?")


# ---- review findings 2026-11-15 ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["What is RAM?", "What is memory?", "What's memory?", "How much memory does the laptop have?",
                                  "How much memory is left on the laptop?", "What's my CPU usage and RAM?", "How much RAM is Chrome using?"])
def test_definitions_other_machines_and_two_part_questions_are_not_the_reading(said):
    assert "of memory is free" not in (quick.handle(said) or ""), said


@pytest.mark.parametrize("said", ["Is my RAM full?", "How much free RAM do I have?", "What's my free memory?",
                                  "How much memory is being used?".replace("being used", "in use")])
def test_more_ways_to_ask_for_the_memory_are_answered(said):
    assert "GB of memory is free" in (quick.handle(said) or ""), said


def test_a_machine_without_the_status_tool_passes_the_turn_on(monkeypatch):
    """The laptop has no get_system_status (DESK_ONLY): the turn goes on to the PC, not 'I couldn't read the PC's status'."""
    monkeypatch.setattr(registry, "names", lambda: [])
    for said in ("How much RAM is free?", "What is my CPU usage?"):
        assert quick.handle(said) is None, said


@pytest.mark.parametrize("said", ["how much RAM is on my PC.", "How much RAM do I have?", "How much RAM is installed?", "How much memory does my PC have?"])
def test_how_much_memory_the_pc_has_is_answered_with_the_total_and_the_free_figure(said):
    reply = quick.handle(said) or ""
    assert "31.5 GB" in reply and "9.1 GB" in reply, said


# ---- "How much room is free?" (his "How much RAM is free?" as Whisper wrote it, live 2026-11-16 23:55): it went to a web search ----------
@pytest.mark.parametrize("said", ["How much room is free?", "How much room is left?", "So how much room do I have?", "How much room is available on my PC?"])
def test_room_in_the_free_frame_reads_both_memory_and_disk(said):
    reply = quick.handle(said) or ""
    assert "9.1 GB of memory is free" in reply and "410 GB of disk is free" in reply, (said, reply)


@pytest.mark.parametrize("said", ["How much space is left on my PC?", "How much disk space is free?", "How much storage do I have left?"])
def test_space_and_storage_are_the_disk(said):
    reply = quick.handle(said) or ""
    assert "410 GB of disk is free" in reply and "of memory is free" not in reply, (said, reply)


@pytest.mark.parametrize("said", ["How much room is in the trunk of a Tesla Model Y?", "How much room do I need for a king bed?",
                                  "The living room is free tonight.", "How much room is free in the car?", "How much space is there on the Moon?"])
def test_room_and_space_elsewhere_are_not_the_reading(said):
    reply = quick.handle(said) or ""
    assert "of disk is free" not in reply and "of memory is free" not in reply, (said, reply)


def test_the_laptop_passes_room_on_to_the_pc(monkeypatch):
    monkeypatch.setattr(registry, "names", lambda: [])
    assert quick.handle("How much room is free?") is None


@pytest.mark.parametrize("said", ["How much room is free?", "How much RAM is free?", "How much space is left on my PC?",
                                  "How much storage do I have left?", "How much memory is in use right now?", "How much battery is left?"])
def test_a_machine_resource_question_is_never_searched_on_the_web(said):
    from core import webintent
    assert webintent.lookup_queries(said) == [], said


@pytest.mark.parametrize("said", ["How much room is in the trunk of a Tesla Model Y?", "How much space is there on the Moon?"])
def test_worldly_room_questions_are_still_searched(said):
    from core import webintent
    assert webintent.lookup_queries(said), said
