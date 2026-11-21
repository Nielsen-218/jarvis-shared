"""Persistent coding knowledge is private, retrieved offline and never executed."""
import pytest

from core import autolearn, skill_memory


@pytest.fixture
def memory_store(monkeypatch):
    data = {}
    monkeypatch.setattr(skill_memory.storage, "load", lambda key, default: data.get(key, default))
    monkeypatch.setattr(skill_memory.storage, "save", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(skill_memory, "_mine", lambda: "household/learned-procedures-pc.json")
    return data


def example():
    return dict(
        language="C++", title="Compile a minimal Unreal class",
        procedure="Create a gameplay C++ class in the project source tree; configure the "
                  "build target and compile with the Unreal project toolchain.",
        source="Epic Unreal Engine C++ programming documentation",
        how_to_test="Build the actual game target in a clean worktree and open the project.",
    )


def test_saved_procedure_survives_new_search_and_has_no_executable_authority(memory_store):
    note = skill_memory.capture(**example())
    assert note["state"] == "captured_unverified"
    assert note["revision"] == 1
    assert "household/learned-procedures-pc.json" in memory_store
    assert skill_memory.search("Unreal C++ compile")[0]["id"] == note["id"]
    assert not hasattr(skill_memory, "execute")


def test_duplicate_does_not_overwrite_and_revision_keeps_history(memory_store):
    first = skill_memory.capture(**example())
    again = skill_memory.capture(**example())
    assert again["duplicate"] and again["id"] == first["id"]
    modified = example()
    modified["procedure"] += " Check warnings and generated binaries."
    second = skill_memory.capture(**modified)
    assert second["revision"] == 2
    assert len(skill_memory.entries()) == 2
    assert first["id"] != second["id"]


def test_synced_server_and_pc_shards_are_read_without_clobbering(memory_store, monkeypatch):
    a = skill_memory.capture(**example())
    monkeypatch.setattr(skill_memory, "_mine", lambda: "household/learned-procedures-server.json")
    modified = example()
    modified["title"] = "Build a second Unreal class"
    b = skill_memory.capture(**modified)
    assert {x["id"] for x in skill_memory.entries()} == {a["id"], b["id"]}


def test_identical_procedure_from_different_sources_retains_both(memory_store):
    a = skill_memory.capture(**example())
    item = example()
    item["source"] = "Second instructor with independent source attribution"
    b = skill_memory.capture(**item)
    assert a["id"] != b["id"] and b["revision"] == 2
    assert len(skill_memory.entries()) == 2


@pytest.mark.parametrize("bad", [
    "password=opensesame", "api_key=123456789", "sk-somerandomtokenwithlength",
    "-----BEGIN PRIVATE KEY-----\nsecret content",
])
def test_secrets_are_never_saved_in_skill_memory(memory_store, bad):
    item = example()
    item["procedure"] += " " + bad
    with pytest.raises(ValueError):
        skill_memory.capture(**item)
    assert skill_memory.entries() == []


def test_unknown_or_blank_procedures_are_rejected(memory_store):
    item = example()
    item["procedure"] = "too short"
    with pytest.raises(ValueError):
        skill_memory.capture(**item)
    assert skill_memory.search("anything") == []


def test_coding_references_queue_once_without_claiming_mastery(monkeypatch):
    data = {}
    monkeypatch.setattr(autolearn.storage, "load", lambda key, default: data.get(key, default))
    monkeypatch.setattr(autolearn.storage, "save", lambda key, value: data.__setitem__(key, value))
    state = autolearn.queue_programming_language("C++")
    assert state["language"] == "c++"
    assert state["queued"] == 1 and state["indexed_sources"] == 0
    assert autolearn.queue_programming_language("cpp")["queued"] == 1
    with pytest.raises(ValueError):
        autolearn.queue_programming_language("made-up-language")


def test_owner_skill_tools_do_not_promote_captured_notes_to_verified(memory_store, monkeypatch):
    from core import users
    from tools import learning, registry
    monkeypatch.setattr(users, "current", lambda: "owner")
    monkeypatch.setattr(users, "get", lambda uid: {"role": "owner"})
    result = learning.jarvis_save_coding_procedure(**example())
    assert "NOT independently tested" in result
    assert "Unreal" in learning.jarvis_find_coding_procedure("Unreal C++")
    for skill in ("jarvis_study_programming", "jarvis_save_coding_procedure", "jarvis_find_coding_procedure"):
        assert skill in registry.names()


def test_household_member_cannot_save_shared_skill(memory_store, monkeypatch):
    from core import users
    from tools import learning
    monkeypatch.setattr(users, "current", lambda: "guest")
    monkeypatch.setattr(users, "get", lambda uid: {"role": "member"})
    assert "Only the household owner" in learning.jarvis_save_coding_procedure(**example())
    assert skill_memory.entries() == []
