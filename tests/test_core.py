"""Fast offline tests: no microphone, camera, GPU or API calls."""
from types import SimpleNamespace

import pytest

from tools import registry


# -- registry ---------------------------------------------------------------------------
def test_strict_tool_limit():
    strict = [s["name"] for s in registry.schemas() if s.get("strict")]
    assert len(strict) <= 20, strict


def test_tool_names_unique_and_described():
    schemas = registry.schemas()
    assert len({s["name"] for s in schemas}) == len(schemas)
    assert all(len(s["description"]) > 10 for s in schemas)


@pytest.mark.parametrize("args, problem", [
    ({}, "missing"),
    ({"list_name": "todo", "text": "x", "extra": 1}, "unknown"),
    ({"list_name": "groceries", "text": "x"}, "one of"),
    ({"list_name": "todo", "text": 5}, "type string"),
])
def test_validation_rejects_bad_args(args, problem):
    result, is_error = registry.run("add_note", args)
    assert is_error and problem in result


def test_bool_is_not_an_integer():
    assert "integer" in (registry.validate("volume", {"percent": True}) or "")


def test_negative_volume_delta_changes_the_current_level(monkeypatch):
    from tools import media

    class Endpoint:
        level = 1.0
        muted = False

        def GetMasterVolumeLevelScalar(self):
            return self.level

        def SetMasterVolumeLevelScalar(self, value, _):
            self.level = value

        def GetMute(self):
            return self.muted

        def SetMute(self, value, _):
            self.muted = bool(value)

    endpoint = Endpoint()
    monkeypatch.setattr(media, "_endpoint", lambda: endpoint)
    assert media.volume(-50) == "Volume is 50%."
    assert media.volume(-1) == "Volume is 50%."  # -1 remains a read-only sentinel
    assert media.volume(-100) == "Volume is 0%."


# -- memory -----------------------------------------------------------------------------
def test_memory_roundtrip():
    from tools import memory

    assert memory.remember("His sister Marie lives in Miami.", "people", False) == "Noted, sir: His sister Marie lives in Miami."
    assert memory.remember("his sister marie lives in miami.", "people", False) == "Already noted, sir."
    assert memory.told_back("My wife is Maya and I live in Springfield.") == "your wife is Maya and you live in Springfield."
    found = memory.recall("sister")
    assert "Marie" in found
    mid = found.split("]")[0].strip("[")
    assert "Forgotten" in memory.forget(mid)
    assert "Nothing" in memory.recall("")


def test_told_back_keeps_quoted_words():
    """His quoted words are read back exactly: only the words around the quote are turned to "you" (2026-11-16)."""
    from tools import memory

    fact = 'the opening line is exactly "I put my life in your hands."'
    assert memory.told_back(fact) == fact
    assert memory.told_back('My motto is “I never quit.”') == 'your motto is “I never quit.”'
    assert memory.told_back('I said "my way" and I meant it.') == 'you said "my way" and you meant it.'
    assert memory.told_back("I don't like mushrooms.") == "you don't like mushrooms."
    assert memory.told_back('I quoted "an unclosed line') == 'you quoted "an unclosed line'


def test_memory_context_has_date_and_facts():
    from tools import memory

    memory.remember("Works night shifts on weekends.", "work", False)
    ctx = memory.context_block()
    assert "Current date and time" in ctx and "night shifts" in ctx


# -- notes ------------------------------------------------------------------------------
def test_lists():
    from tools import notes

    notes.add_note("buy eggs", "shopping")
    notes.add_note("call mom", "todo")
    listing = notes.read_list("shopping", False)
    assert "eggs" in listing and "mom" not in listing
    item = listing.split("]")[0].strip("[")
    notes.update_list([item], "done")
    assert "empty" in notes.read_list("shopping", False)
    assert "[done]" in notes.read_list("shopping", True)


def test_search_notes_combines_note_lists_and_remembered_facts():
    from tools import memory, notes

    notes.add_note("Dentist appointment Tuesday at nine", "todo")
    memory.remember("My dentist is Dr. Patel.", "health", False)
    result = notes.search_notes("dentist appointment")
    assert "Saved notes:" in result and "Dentist appointment" in result
    assert "Remembered facts:" in result and "Dr. Patel" in result
    assert "did not search the internet" in notes.search_notes("unrelated topic")


# -- usage / budget ---------------------------------------------------------------------
def test_usage_cost_and_budget(monkeypatch):
    from core import usage

    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=2))
    cost = usage.record("claude-sonnet-5-5", u)
    assert cost == pytest.approx(2.0 + 1.0 + 0.02)
    monkeypatch.setenv("JARVIS_MONTHLY_BUDGET", "3")
    assert usage.over_budget()
    monkeypatch.setenv("JARVIS_MONTHLY_BUDGET", "50")
    assert not usage.over_budget()
    assert "This month: $3.02" in usage.summary()


def test_storage_survives_corrupt_file(tmp_data):
    from core import storage

    (tmp_data / "users" / "owner").mkdir(parents=True)
    (tmp_data / "users" / "owner" / "notes.json").write_text("{not json", encoding="utf-8")
    assert storage.load("notes.json", []) == []


# -- language ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, creole", [
    ("Bonjou mesye, kijan ou ye jodi a?", True),
    ("Mwen la, m ap tann ou.", True),
    ("Good evening, sir.", False),
    ("I see a black tee.", False),
])
def test_creole_detection(text, creole):
    from audio.language import is_creole

    assert is_creole(text) is creole


# -- speech end detection -----------------------------------------------------------------
@pytest.mark.parametrize("text, unfinished", [
    ("Open the", True), ("Can you bring me to", True), ("I want to, um,", True),
    ("What time is it?", False), ("Open Brave.", False),
])
def test_sounds_unfinished(text, unfinished):
    import main

    assert main.sounds_unfinished(text) is unfinished


@pytest.mark.parametrize("heard, command", [
    ("Hey Jarvis, open Spotify.", "open Spotify."), ("hey charvis", ""), ("No, hey Jarvis.", ""),
    ("Okay jervis what time is it", "what time is it"), ("Jarvis", ""),
    ("No, I don't want that.", "No, I don't want that."),          # no name: nothing stripped
    ("Travis Scott is playing", "Travis Scott is playing"),        # a real name, not a mishear
])
def test_wake_prefix_strips_name_and_misspellings_only(heard, command):
    import main

    assert main.WAKE_PREFIX.sub("", heard).strip() == command


@pytest.mark.parametrize("command, fixed", [
    ("repair my phone.", "pair my phone"), ("Prepare my phone", "pair my phone"), ("Pierre my phone", "pair my phone"),
    ("hear my phone", "pair my phone"), ("pair my phone", "pair my phone"),
    ("prepare my song", "prepare my song"),                          # not a phone: left alone
    ("can you repair my phone screen", "can you repair my phone screen"),  # longer sentence: left alone
])
def test_fix_misheard_only_rewrites_exact_pairing_mishears(command, fixed):
    import main

    assert main.fix_misheard(command) == fixed


# -- fashion / studio ---------------------------------------------------------------------
def test_style_profile_and_outfit_log():
    from tools import fashion

    fashion.update_style_profile("sizes", "L tops, 34x34 jeans")
    assert "34x34" in fashion.get_style_profile()
    fashion.log_outfit("black tee, grey sweats", [], 7, "home")
    assert "rated 7/10" in fashion.recent_outfits(3)


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "file:///c:/windows/win.ini", "http://192.168.1.1/"])
def test_garment_download_blocks_local_urls(url):
    from tools import studio

    with pytest.raises(ValueError):
        studio._get(url)


def test_person_output_size_keeps_aspect():
    from PIL import Image

    from imaging.person import output_size

    w, h = output_size(Image.new("RGB", (480, 720)))
    assert w % 16 == 0 and h % 16 == 0 and abs(w / h - 480 / 720) < 0.03


def test_hybrid_brain_routing():
    from brain.llm import _COMPLEX, ESCALATE, FAST_MODEL

    assert FAST_MODEL
    for quick in ["what time is it", "what's the weather tomorrow", "tell me a joke", "remind me at 5 to call mom"]:
        assert not _COMPLEX.search(quick), quick
    for hard in ["build me a meal plan", "my homelab is broken", "fix the wifi", "write me an essay", "debug this code",
                 "uninstall discord", "make me a workout plan"]:
        assert _COMPLEX.search(hard), hard
    assert ESCALATE["name"] == "use_expert_brain" and "input_schema" in ESCALATE
