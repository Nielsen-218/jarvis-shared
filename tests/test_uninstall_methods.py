"""The persistent uninstall: picks the right silent switch per installer, and falls back to the next
method when one fails (winget, the program's own uninstaller, Store-app removal)."""
import subprocess

import pytest

from core import approvals, users
from tools import software


@pytest.mark.parametrize("entry, expected", [
    ({"UninstallString": r'"C:\Program Files\Proton\VPN\unins000.exe"'},
     r'"C:\Program Files\Proton\VPN\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART'),
    ({"UninstallString": r"C:\Steam\uninstall.exe"}, r"C:\Steam\uninstall.exe /S"),
    ({"UninstallString": "MsiExec.exe /X{12345678-1234-1234-1234-123456789012}", "WindowsInstaller": 1},
     "msiexec /x {12345678-1234-1234-1234-123456789012} /qn /norestart"),
    ({"UninstallString": r"C:\Users\n\AppData\Local\Discord\Update.exe --uninstall"},
     r"C:\Users\n\AppData\Local\Discord\Update.exe --uninstall -s"),
    ({"UninstallString": "x", "QuietUninstallString": "x /quiet"}, "x /quiet"),
    ({"UninstallString": r'"C:\Windows\SysWOW64\RunDll32.EXE" "nvi2.dll",UninstallPackage X'},
     r'"C:\Windows\SysWOW64\RunDll32.EXE" "nvi2.dll",UninstallPackage X'),
])
def test_silent_switches(entry, expected):
    assert software._silent_uninstall_command(entry) == expected


def test_falls_back_to_own_uninstaller(monkeypatch):
    """winget fails (e.g. a user-scope app from an admin session) -> its own uninstaller removes it."""
    state = {"installed": True}
    ran = []
    monkeypatch.setattr(software, "find_installed",
                        lambda n: [{"Name": "Proton Drive", "Id": "Proton.ProtonDrive"}] if state["installed"] else [])
    monkeypatch.setattr(software, "_winget",
                        lambda *a, timeout=120: subprocess.CompletedProcess(a, 0x8A15007D, "", ""))
    monkeypatch.setattr(software, "_registry_programs", lambda n: [
        {"DisplayName": "Proton Drive", "UninstallString": r"C:\Users\n\AppData\Local\ProtonDrive\unins000.exe"}]
        if state["installed"] else [])
    monkeypatch.setattr(software, "_appx", lambda n, remove: "")

    def run(cmd, **kw):
        ran.append(cmd)
        state["installed"] = False
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    monkeypatch.setattr(software.subprocess, "run", run)
    approvals.on_user_message(users.current(), "uninstall all proton applications")
    assert software.uninstall_program("Proton Drive") == "Uninstalled Proton Drive (with its own uninstaller)."
    assert ran and "/VERYSILENT" in ran[0]


def test_reports_everything_it_tried(monkeypatch):
    monkeypatch.setattr(software, "find_installed", lambda n: [{"Name": "Stubborn", "Id": "X.Stubborn"}])
    monkeypatch.setattr(software, "_winget", lambda *a, timeout=120: subprocess.CompletedProcess(a, 0x8A150030, "", ""))
    monkeypatch.setattr(software, "_registry_programs", lambda n: [{"DisplayName": "Stubborn", "UninstallString": "s.exe"}])
    monkeypatch.setattr(software, "_appx", lambda n, remove: "")
    monkeypatch.setattr(software.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b""))
    monkeypatch.setattr(software, "is_admin", lambda: True)
    approvals.on_user_message(users.current(), "uninstall Stubborn")
    out = software.uninstall_program("Stubborn")
    assert out.startswith("Couldn't remove Stubborn yet") and "package manager" in out and "own uninstaller" in out
    assert "control_computer" in out and "without asking again" in out
