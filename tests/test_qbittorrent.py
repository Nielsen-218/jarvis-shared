"""Offline tests for the qBittorrent tools (the Web API is faked)."""
import json

from tools import qbittorrent as qb

TORRENTS = [
    {"name": "ubuntu-24.04-desktop-amd64.iso", "hash": "aaa111", "progress": 0.5, "size": 6 * 1024**3,
     "state": "downloading", "dlspeed": 2 * 1024**2, "eta": 600},
    {"name": "debian-12.iso", "hash": "bbb222", "progress": 1.0, "size": 700 * 1024**2,
     "state": "uploading", "dlspeed": 0, "eta": 8640000},
]


def fake(monkeypatch):
    calls = []

    def api(path, data=None, retry=True):
        calls.append((path, data))
        return json.dumps(TORRENTS) if path.startswith("torrents/info") else "Ok."
    monkeypatch.setattr(qb, "_api", api)
    return calls


def test_list(monkeypatch):
    fake(monkeypatch)
    out = qb.qbit_list()
    assert "ubuntu-24.04-desktop-amd64.iso - 50% of 6.0 GB" in out and "10 min left" in out
    assert "debian-12.iso - 100%" in out and "min left" not in out.splitlines()[1]


def test_add(monkeypatch):
    calls = fake(monkeypatch)
    assert "Added" in qb.qbit_add("magnet:?xt=urn:btih:abc", "linux", "")
    assert calls[-1] == ("torrents/add", {"urls": "magnet:?xt=urn:btih:abc", "savepath": "/downloads/linux"})
    assert "inside downloads" in qb.qbit_add("magnet:?xt=urn:btih:abc", "../etc", "")
    assert "isn't a magnet" in qb.qbit_add("file:///etc/passwd", "", "")


def test_control_and_confirmation(monkeypatch):
    calls = fake(monkeypatch)
    assert qb.qbit_control("ubuntu", "pause", False).startswith("Pause")
    assert calls[-1] == ("torrents/stop", {"hashes": "aaa111"})
    out = qb.qbit_control("debian", "remove_with_files", False)
    assert out.startswith("NEEDS_CONFIRMATION") and not any(c[0] == "torrents/delete" for c in calls)
    assert qb.qbit_control("debian", "remove_with_files", True).startswith("NEEDS_CONFIRMATION")  # no yes yet
    assert not any(c[0] == "torrents/delete" for c in calls)
    from core import approvals, users

    qb.qbit_control("debian", "remove_with_files", False)
    approvals.on_user_message(users.current(), "go ahead")
    qb.qbit_control("debian", "remove_with_files", True)
    assert calls[-1] == ("torrents/delete", {"hashes": "bbb222", "deleteFiles": "true"})
    qb.qbit_control("all", "remove", False)
    assert calls[-1] == ("torrents/delete", {"hashes": "aaa111|bbb222", "deleteFiles": "false"})
    assert "No torrent" in qb.qbit_control("nothing-like-this", "resume", False)


def test_missing_login(monkeypatch):
    monkeypatch.delenv("QBIT_USER", raising=False)
    try:
        qb._login()
    except RuntimeError as e:
        assert "QBIT_USER" in str(e)
