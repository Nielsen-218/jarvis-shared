"""data/ is shared between the PC and the laptop (Syncthing). A store that is rewritten although nothing changed, or
written with the platform's line endings, is a conflict copy waiting to happen: both machines re-queued the study
topics at the same moment (CRLF on Windows, LF on Linux) and Syncthing kept a copy of each side every time (live
2026-10-30: household/autolearn/queue.json, users/owner/research/state.sqlite3). One machine writes the learning
stores; a write that changes nothing touches nothing; concurrent writers never leave a torn file."""
import importlib
import json
import threading
import time

import pytest

from core import autolearn, scheduler, storage


def _mtime(path):
    return path.stat().st_mtime_ns


def test_saving_the_same_value_leaves_the_file_untouched():
    storage.save("things.json", {"a": [1, 2], "name": "Kreyòl"})
    path = storage.path("things.json")
    before, data = _mtime(path), path.read_bytes()
    time.sleep(0.02)
    storage.save("things.json", {"a": [1, 2], "name": "Kreyòl"})
    assert _mtime(path) == before and path.read_bytes() == data
    storage.save("things.json", {"a": [1, 2, 3], "name": "Kreyòl"})
    assert path.read_bytes() != data and storage.load("things.json", None) == {"a": [1, 2, 3], "name": "Kreyòl"}


def test_the_bytes_are_the_same_on_every_platform():
    storage.save("rows.json", [{"n": i, "text": "café"} for i in range(5)])
    raw = storage.path("rows.json").read_bytes()
    assert b"\r" not in raw and raw.count(b"\n") > 5 and "café".encode() in raw
    assert json.loads(raw.decode("utf-8")) == [{"n": i, "text": "café"} for i in range(5)]
    storage.save_global("settings-like.json", {"k": "v"})
    assert b"\r" not in (storage.DATA / "settings-like.json").read_bytes()


def test_concurrent_writers_never_leave_a_torn_or_stray_file():
    payloads = [{"writer": w, "rows": list(range(w * 10, w * 10 + 40))} for w in range(12)]
    errors = []

    def write(p):
        try:
            for _ in range(15):
                storage.save("shared.json", p)
                assert storage.load("shared.json", None) in payloads
        except Exception as e:                                  # noqa: BLE001 - reported below
            errors.append(e)
    threads = [threading.Thread(target=write, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert storage.load("shared.json", None) in payloads
    assert not list(storage.path("shared.json").parent.glob("*.tmp"))


def test_requeueing_study_topics_rewrites_nothing_and_a_restart_is_silent():
    for topic in autolearn.TOPICS:
        autolearn.queue_topic(topic)
    path = storage.path(autolearn.STORE)
    before, data = _mtime(path), path.read_bytes()
    queued = autolearn.status()["queued"]
    time.sleep(0.02)
    importlib.reload(autolearn)                                 # a restart: no module state survives
    for topic in autolearn.TOPICS:
        autolearn.queue_topic(topic)
    autolearn.queue_programming_language("rust")
    autolearn.queue_programming_language("rust")
    assert autolearn.status()["queued"] > queued                # the new curriculum was added once...
    after = _mtime(path)
    time.sleep(0.02)
    for topic in autolearn.TOPICS:
        autolearn.queue_topic(topic)
    autolearn.queue_programming_language("rust")
    assert _mtime(path) == after                                # ...and nothing is rewritten after that
    assert b"\r" not in path.read_bytes() and path.read_bytes() != data


def test_a_status_read_of_the_idle_queue_writes_nothing_to_the_research_database():
    from core import tutorial_idle, users
    users.set_current("owner")
    tutorial_idle.configure()
    path = storage.path("research/state.sqlite3")
    before, data = _mtime(path), path.read_bytes()
    time.sleep(0.02)
    assert tutorial_idle.configure()["enabled"] is False
    assert _mtime(path) == before and path.read_bytes() == data
    assert not path.with_name(path.name + "-journal").exists()
    tutorial_idle.configure(topic="soldering basics")
    assert path.read_bytes() != data and tutorial_idle.configure()["queued"] == ["soldering basics"]


@pytest.mark.parametrize("role, owns", [("desk", False), ("server", True), ("all", True)])
def test_one_machine_owns_the_shared_learning_stores(monkeypatch, role, owns):
    monkeypatch.setattr(scheduler, "ROLE", role)
    assert scheduler.owns_shared_learning() is owns
