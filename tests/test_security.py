"""The PC security checkup: its detection logic flags the real persistence tricks and leaves legit
autostart entries alone, and the skill is wired into the registry like every other.

The decisive case is the one that actually happened (2026-10-24): a scheduled task named like an
Edge updater, authored by the owner, launching a Microsoft-signed Windows binary that had been
renamed to a random string and dropped loose in AppData\\Roaming. These tests pin that it would be
caught — and that Spotify/OneDrive-style entries that legitimately live under AppData are not.
"""
import os

from tools import registry, security


# -- the skill is loaded like every other -----------------------------------------------------------
def test_security_checkup_is_registered():
    assert "security_checkup" in registry.names()


def test_security_checkup_runs_and_returns_text():
    # Read-only; safe to run for real. On the always-on laptop it explains it's PC-only.
    out = security.security_checkup()
    assert isinstance(out, str) and out.strip()
    assert "Security checkup" in out or "Windows PC" in out


def test_off_windows_it_says_pc_only(monkeypatch):
    monkeypatch.setattr(security.os, "name", "posix")
    out = security.security_checkup()
    assert "Windows PC" in out


# -- the disguised-updater scheduled task (the real incident) ----------------------------------------
def test_the_renamed_edge_updater_task_is_flagged():
    reasons = security._launch_reasons(
        "MicrosoftEdgeUpdateTaskMachine4B911ED9331CC2D3", "alex",
        r"C:\Users\you\AppData\Roaming\riighhh", "")
    assert reasons, "the disguised updater task must be flagged"
    joined = " ".join(reasons).lower()
    assert "roaming" in joined or "renamed" in joined or "updater" in joined


def test_temp_and_downloads_launches_are_flagged():
    assert security._launch_reasons("x", "", r"C:\Users\you\AppData\Local\Temp\abc.exe", "")
    assert security._launch_reasons("x", "", r"C:\Users\you\Downloads\setup_crack.exe", "")


def test_hidden_encoded_command_is_flagged():
    assert security._launch_reasons(
        "task", "", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-nop -w hidden -enc SQBFAFgA")


# -- legit autostart entries are left alone ----------------------------------------------------------
def test_legit_appdata_apps_are_not_flagged():
    # OneDrive (AppData\Local\Microsoft\OneDrive) and Spotify (a named subfolder of Roaming, real .exe)
    assert not security._launch_reasons("OneDrive", "Microsoft Corporation",
                                        r"C:\Users\you\AppData\Local\Microsoft\OneDrive\OneDrive.exe", "")
    assert not security._launch_reasons("Spotify", "",
                                        r"C:\Users\you\AppData\Roaming\Spotify\Spotify.exe", "")


def test_program_files_updater_is_not_flagged():
    assert not security._launch_reasons("GoogleUpdateTaskMachineCore", "Google LLC",
                                        r"C:\Program Files (x86)\Google\Update\GoogleUpdate.exe", "")


# -- the building-block heuristics -------------------------------------------------------------------
def test_suspicious_exe_name():
    assert security._suspicious_exe_name(r"C:\Users\you\AppData\Roaming\riighhh")   # no extension
    assert security._suspicious_exe_name(r"C:\x\payload.dat")                          # data disguise
    assert not security._suspicious_exe_name(r"C:\Program Files\App\app.exe")
    assert not security._suspicious_exe_name(r"C:\x\script.ps1")


def test_exe_from_command():
    assert security._exe_from_command('"C:\\Program Files\\A\\a.exe" --flag') == r"C:\Program Files\A\a.exe"
    assert security._exe_from_command(r"C:\Windows\notepad.exe /x") == r"C:\Windows\notepad.exe"
    assert security._exe_from_command("") == ""


def test_updater_name_detection():
    assert security._looks_like_updater("MicrosoftEdgeUpdateTaskMachineX")
    assert security._looks_like_updater("GoogleUpdate")
    assert not security._looks_like_updater("MyBackupJob")
