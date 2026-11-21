"""winget-based install/uninstall (winget itself is faked)."""
import subprocess

from core import approvals, users
from tools import software

LIST = """Name                Id                         Version  Available Source
-----------------------------------------------------------------------------
Discord             Discord.Discord            1.0.9     1.0.10   winget
"""
SEARCH = """Name                        Id                          Version  Source
------------------------------------------------------------------------
Jellyfin VLC Bridge         CrySer66.JellyfinVlcBridge  1.18.0   winget
VLC media player            VideoLAN.VLC                3.0.24   winget
VLC media player (Nightly)  VideoLAN.VLC.Nightly        4.0.0.0  winget
"""


def fake(monkeypatch, outputs, codes=None):
    """Fake winget. After a successful uninstall the program disappears from 'list'."""
    calls, state = [], {"gone": False}

    def run(*args, timeout=120):
        calls.append(args)
        if args[0] == "list" and state["gone"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        code = (codes or {}).get(args[0], 0)
        if args[0] == "uninstall" and code == 0:
            state["gone"] = True
        return subprocess.CompletedProcess(args, code, outputs.get(args[0], ""), "")
    monkeypatch.setattr(software, "_winget", run)
    monkeypatch.setattr(software, "_registry_programs", lambda n: [])
    monkeypatch.setattr(software, "_appx", lambda n, remove: "")
    return calls


def test_parse_and_pick():
    rows = software._table(SEARCH)
    assert [r["Id"] for r in rows] == ["CrySer66.JellyfinVlcBridge", "VideoLAN.VLC", "VideoLAN.VLC.Nightly"]
    assert software._pick(rows, "VLC")[0]["Id"] == "VideoLAN.VLC"


def test_uninstall_runs_on_direct_command(monkeypatch):
    calls = fake(monkeypatch, {"list": LIST})
    approvals.on_user_message(users.current(), "uninstall discord")
    assert software.uninstall_program("Discord") == "Uninstalled Discord."
    un = [c for c in calls if c[0] == "uninstall"]
    assert un[0][:3] == ("uninstall", "--id", "Discord.Discord") and "--silent" in un[0]
    assert calls[-1][0] == "list"  # and it checked that Discord is really gone


def test_uninstall_asks_when_not_commanded(monkeypatch):
    calls = fake(monkeypatch, {"list": LIST})
    approvals.on_user_message(users.current(), "my PC feels cluttered")
    assert software.uninstall_program("Discord").startswith("NEEDS_CONFIRMATION")
    assert not any(c[0] == "uninstall" for c in calls)
    approvals.on_user_message(users.current(), "yes")
    assert software.uninstall_program("Discord") == "Uninstalled Discord."


def test_admin_needed_is_explained(monkeypatch):
    fake(monkeypatch, {"list": LIST}, {"uninstall": 0x8A150056})
    monkeypatch.setattr(software, "is_admin", lambda: False)
    approvals.on_user_message(users.current(), "uninstall Discord")
    out = software.uninstall_program("Discord")
    assert out.startswith("Couldn't remove Discord yet") and "approval" in out and "Admin mode is off" in out


def test_install_picks_stable(monkeypatch):
    calls = fake(monkeypatch, {"search": SEARCH})
    assert software.install_program("VLC") == "Installed VLC media player."
    assert calls[-1][:3] == ("install", "--id", "VideoLAN.VLC")


def test_launch_as_user_refuses_blank_so_it_cant_pop_this_pc(monkeypatch):
    """`explorer.exe` with a blank/bare arg opens the "This PC" window; a stray periodic caller was doing that
    every few minutes. launch_as_user must refuse empty/invalid targets and only Popen a real one."""
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]) or None)
    for blank in ("", "   ", '""', "explorer", "explorer.exe", "This PC", "shell:MyComputerFolder", None):
        software.launch_as_user(blank)                 # must not launch Explorer with nothing to open
    assert calls == []
    software.launch_as_user(r"C:\Windows\notepad.exe")  # a real target still opens
    assert calls == [["explorer.exe", r"C:\Windows\notepad.exe"]]
