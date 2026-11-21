"""Offline tests for the homelab tools (the Pi is replaced by a fake ssh)."""
import pytest

from tools import homelab


@pytest.fixture
def fake_ssh(monkeypatch):
    calls = []

    def run(command, timeout=30):
        calls.append(command)
        if "docker ps -a --format '{{.Names}}'" in command:
            return "jellyfin\nmetube\nportainer"
        return "ok"

    monkeypatch.setattr(homelab, "ssh", run)
    return calls


def test_paths_never_leave_the_drive():
    root = homelab.MEDIA_ROOT
    assert homelab.media_path("movies/Classics") == f"{root}/movies/Classics"
    assert homelab.media_path("../../etc/passwd").startswith(root)
    assert homelab.media_path("movies/../../../etc").startswith(root)
    assert homelab.media_path("") == root


def test_delete_needs_confirmation_and_protects_main_folders(fake_ssh):
    assert "won't delete" in homelab.delete_item("movies", True)
    assert "won't delete" in homelab.delete_item("", True)
    from core import approvals, users

    assert homelab.delete_item("movies/old trailer.mp4", False).startswith("NEEDS_CONFIRMATION")
    # The AI claiming confirmed=true is not enough: nothing is deleted without the person's own yes.
    assert homelab.delete_item("movies/old trailer.mp4", True).startswith("NEEDS_CONFIRMATION")
    assert not any("rm -rf" in c for c in fake_ssh)
    approvals.on_user_message(users.current(), "Yes, delete it.")
    assert homelab.delete_item("movies/other file.mp4", True).startswith("NEEDS_CONFIRMATION")  # a different item
    approvals.on_user_message(users.current(), "no, I meant the trailer")  # a "no" clears that question
    homelab.delete_item("movies/old trailer.mp4", False)
    approvals.on_user_message(users.current(), "yes")
    assert homelab.delete_item("movies/old trailer.mp4", True) == "Deleted movies/old trailer.mp4."
    assert homelab.delete_item("movies/old trailer.mp4", True).startswith("NEEDS_CONFIRMATION")  # yes is used up
    assert any(c.startswith("rm -rf -- ") and "'" in c for c in fake_ssh)  # quoted path


def test_restart_only_known_and_unprotected_apps(fake_ssh):
    assert "No app called" in homelab.restart_app("hacker; rm -rf /")
    assert "manages the others" in homelab.restart_app("portainer")
    assert homelab.restart_app("jellyfin").startswith("Restarted jellyfin")


def test_jellyfin_needs_a_key(monkeypatch):
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    # Public skill gives actionable setup guidance; raw request helper still refuses.
    assert "isn't configured" in homelab.jellyfin_overview()
    with pytest.raises(RuntimeError, match="API key"):
        homelab._jf("/Library/VirtualFolders")


def test_public_domain_download_refuses_other_collections(monkeypatch):
    import io
    import json
    import urllib.request

    meta = {"metadata": {"collection": ["some_other_collection"], "title": "X"}, "files": []}
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=20: io.BytesIO(json.dumps(meta).encode()))
    assert "isn't in" in homelab.public_domain_download("someitem")


def test_health_check_parses_problems(monkeypatch):
    out = ("PROBLEM|mount:immich_server|immich_server lost its connection to the media drive\n"
           "PROBLEM|mount:immich_server|immich_server lost its connection to the media drive\n"
           "PROBLEM|down:metube|metube is exited\nsome noise")
    monkeypatch.setattr(homelab, "ssh", lambda c, timeout=30: out)
    assert homelab.health_problems() == {
        "mount:immich_server": "immich_server lost its connection to the media drive",
        "down:metube": "metube is exited"}
    report = homelab.homelab_health()
    assert report.count("immich_server") == 1 and "repair_drive_mounts" in report
    monkeypatch.setattr(homelab, "ssh", lambda c, timeout=30: "")
    assert homelab.homelab_health().startswith("All good")


def test_repair_restarts_only_broken_apps(monkeypatch):
    calls, state = [], {"broken": True}

    def run(command, timeout=30):
        calls.append(command)
        if command.startswith("docker restart"):
            state["broken"] = False
            return ""
        return "PROBLEM|mount:jellyfin|x\nPROBLEM|mount:portainer|x" if state["broken"] else ""
    monkeypatch.setattr(homelab, "ssh", run)
    assert homelab.repair_drive_mounts() == "Restarted jellyfin. All reconnected."
    assert "docker restart jellyfin" in calls and not any("portainer" in c and "restart" in c for c in calls)


def test_watchdog_alerts_once_and_on_recovery(monkeypatch, tmp_data):
    from core import notify, scheduler

    sent, problems = [], {"down:metube": "metube is exited"}
    monkeypatch.setattr(homelab, "health_problems", lambda: dict(problems))
    monkeypatch.setattr(notify, "notify", lambda t, m, uid=None, category="jarvis", urgent=None: sent.append((t, m, uid, category)))
    s = scheduler.Scheduler(say=lambda t: None, ask=lambda t: None)
    s._homelab()
    s._homelab()
    assert sent == [("Homelab problem", "metube is exited.", "owner", "alerts")]
    problems.clear()
    s._homelab()
    assert sent[-1] == ("Homelab OK again", "Fixed: metube is exited.", "owner", "alerts")
