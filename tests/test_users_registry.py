"""users.json is a shared (Syncthing) file that two machines read-modify-write. Cross-device audit E (2026-11-13): a
Syncthing conflict copy is never read and is reported once; a push-subscription change that changes nothing writes nothing."""
import json

import pytest

from core import appserver, storage, users


@pytest.fixture(autouse=True)
def fresh_conflict_state(monkeypatch):
    monkeypatch.setattr(users, "_conflicts_seen", set())
    monkeypatch.setattr(users, "_conflicts_checked", -1e9)


def _conflict_copy(name="users.sync-conflict-20261113-101500-ABCDEFG.json", registry=None):
    path = storage.DATA / name
    path.write_text(json.dumps(registry or {"users": {"intruder": {"name": "X", "role": "owner", "devices": []}}, "sync_since": "2026-11-13T00:00:00.000000"}), encoding="utf-8")
    return path


def test_a_syncthing_conflict_copy_is_merged_archived_and_reported_once(capsys):
    token = appserver.add_device("owner", "phone")
    _conflict_copy()
    assert "intruder" in users.all_users() and appserver.find_device(token)[0] == "owner"   # merged (no longer ignored)
    assert not list(storage.DATA.glob("users.sync-conflict-*"))
    users._conflicts_checked = -1e9
    users.all_users()
    lines = [line for line in capsys.readouterr().err.splitlines() if "conflict" in line]
    assert len(lines) == 1 and "users.sync-conflict-20261113-101500-ABCDEFG.json" in lines[0] and "archived" in lines[0]


def test_a_second_conflict_copy_is_reported_too(capsys):
    _conflict_copy()
    users.all_users()
    _conflict_copy("users.sync-conflict-20261113-111500-HIJKLMN.json")
    users._conflicts_checked = -1e9
    users.all_users()
    assert len([line for line in capsys.readouterr().err.splitlines() if "conflict" in line]) == 2


def test_no_conflict_copy_says_nothing(capsys):
    users.all_users()
    assert "conflict" not in capsys.readouterr().out


def test_the_check_runs_at_most_once_a_minute(monkeypatch):
    calls = []
    real = type(storage.DATA).glob
    monkeypatch.setattr(type(storage.DATA), "glob", lambda self, pattern: calls.append(pattern) or real(self, pattern))
    for _ in range(5):
        users.all_users()
    assert len(calls) == 1


def test_set_push_with_nothing_changed_writes_nothing(monkeypatch):
    appserver.add_device("owner", "phone")
    device = users.get("owner")["devices"][0]["id"]
    appserver.set_push("owner", device, {"endpoint": "https://e"})
    writes = []
    real = storage.save_global
    monkeypatch.setattr(storage, "save_global", lambda *a, **k: writes.append(a) or real(*a, **k))
    appserver.set_push("owner", device, {"endpoint": "https://e"})          # the phone re-subscribes with the same subscription
    appserver.set_push("owner", "no-such-device", None)                     # a stale or unknown device
    users.update("owner", name=users.get("owner")["name"])                  # an update that changes no field
    assert writes == []
    appserver.set_push("owner", device, None)                               # a real change still goes through
    assert len(writes) == 1 and users.get("owner")["devices"][0]["push"] is None


def test_manage_pair_prints_only_the_pairing_line_on_stdout_when_a_conflict_copy_exists(capsys):
    """Live 2026-11-13: three conflict warnings came before the pairing code on stdout and broke scripts that parse it."""
    import manage
    for stamp in ("101500", "111500", "121500"):
        _conflict_copy(f"users.sync-conflict-20261113-{stamp}-ABCDEFG.json")
    manage.main(["pair"])
    out, err = capsys.readouterr()
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1 and lines[0].startswith("Pairing code for ")
    assert err.count("conflict copy users.sync-conflict") == 3, "the notice still goes to the error channel, once per copy"


def test_no_stderr_means_no_notice_and_nothing_on_stdout(capsys, monkeypatch):
    _conflict_copy()
    monkeypatch.setattr("sys.stderr", None)
    users.all_users()
    assert capsys.readouterr().out == ""
