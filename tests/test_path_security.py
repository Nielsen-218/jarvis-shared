"""Path security (PR #216 review): owner space is explicit, canonical and junction-aware, for file verbs AND terminal steps;
opening a script or program is a terminal-class action that never runs unasked."""
import os
import subprocess
import sys

import pytest

from core import authz, pcagent, pcverbs

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths, junctions and the real system folders")
HOME = os.path.expanduser("~")
SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")


def _junction(link, target):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"could not make a junction: {r.stderr or r.stdout}")


@pytest.mark.parametrize("path", [
    SYSROOT, os.path.join(SYSROOT, "notepad.exe"), os.path.join(SYSROOT, "System32", "drivers", "etc", "hosts"),
    os.environ.get("ProgramFiles", r"C:\Program Files"), os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "x", "y.txt"),
    os.environ.get("ProgramData", r"C:\ProgramData") + r"\x.txt",
    "C:\\", r"C:\some-folder-at-the-root\x.txt",
    os.path.join(os.path.dirname(HOME), "Public", "Documents", "x.txt"), os.path.join(os.path.dirname(HOME), "Default", "x.txt"),
    os.path.join(os.path.dirname(HOME), "SomeoneElse", "Documents", "x.txt"),
    os.path.join(HOME, "AppData", "Roaming", "x.txt"), os.path.join(HOME, ".ssh", "config"), os.path.join(HOME, ".aws", "x"),
    os.path.join(HOME, "Documents", "..", "..", "SomeoneElse", "x.txt"),
    r"\\server\share\x.txt", "\\\\?\\" + os.path.join(HOME, "Documents", "x.txt"), "\\\\.\\PhysicalDrive0",
    os.path.join(HOME, "Documents", "a.txt:hidden-stream"), r"%USERPROFILE%\Documents\x.txt", r"$env:USERPROFILE\x.txt", "",
])
def test_outside_owner_space(path):
    assert not pcverbs._in_user_space(path), path


@pytest.mark.parametrize("path", [HOME, os.path.join(HOME, "Documents", "x.txt"), os.path.join(HOME, "Downloads"),
                                  os.path.join(HOME, "LivingShadowWorld", "LivingShadowWorld.uproject")])
def test_inside_owner_space(path):
    assert pcverbs._in_user_space(path), path


def test_other_fixed_drives_count_but_not_their_roots_or_system_folders():
    others = [r for r in pcverbs.user_roots() if r != HOME]
    if not others:
        pytest.skip("no other fixed drive on this PC")
    d = others[0]
    assert not pcverbs._in_user_space(d), "a bare drive root is not a working folder"
    assert pcverbs._in_user_space(os.path.join(d, "Projects", "x.sln"))
    assert not pcverbs._in_user_space(os.path.join(d, "Windows", "x"))
    assert not pcverbs._in_user_space(os.path.join(d, "$Recycle.Bin", "x"))


def test_a_junction_inside_owner_space_that_points_at_windows_is_windows(tmp_path, monkeypatch):
    root = tmp_path / "owner"
    root.mkdir()
    (root / "real").mkdir()
    _junction(root / "escape", SYSROOT)
    monkeypatch.setattr(pcverbs, "owner_roots", lambda: [pcverbs._real(str(root))])
    assert pcverbs._in_user_space(str(root / "real" / "x.txt"))
    assert not pcverbs._in_user_space(str(root / "escape" / "notepad.exe")), "the junction leads into C:\\Windows"
    assert not pcverbs._in_user_space(str(root / "escape" / "new-folder"))


def test_the_terminal_gate_follows_junctions_too(tmp_path):
    """Before: the allowlist compared paths as text, so 'mkdir <owner>\\escape\\x' was a T1 change inside the owner's
    folder while it would really create C:\\Windows\\x (Jarvis runs elevated)."""
    from jarvis_pc.guardrails.tiers import PathAllowlist, classify
    root = tmp_path / "owner"
    root.mkdir()
    _junction(root / "escape", SYSROOT)
    allow = PathAllowlist([str(root)])
    assert int(classify(f"mkdir {root}\\plain", cwd=str(root), allowlist=allow).tier) == 1
    assert int(classify(f"mkdir {root}\\escape\\jarvis-probe", cwd=str(root), allowlist=allow).tier) == 2
    assert not os.path.exists(os.path.join(SYSROOT, "jarvis-probe"))


def test_a_terminal_change_under_appdata_asks_first():
    assert pcverbs.terminal_tier(f"mkdir {HOME}\\Documents\\jarvis-probe") == 1
    assert pcverbs.terminal_tier(f"mkdir {HOME}\\AppData\\Roaming\\jarvis-probe") >= 2
    assert authz.effect({"tool": "run_terminal", "arguments": {"command": f"mkdir {HOME}\\AppData\\Local\\x"}}) == "irreversible"


# ----------------------------------------------------------------------------------------------- scripts and programs
@pytest.mark.parametrize("name", ["run.bat", "run.cmd", "run.ps1", "tool.exe", "app.lnk", "script.py", "setup.msi", "page.hta",
                                  "x.vbs", "x.js", "x.reg", "x.scr", "x.url"])
def test_opening_code_is_irreversible_and_never_an_ordinary_open(name):
    assert pcverbs.runs_code(name)
    assert authz.effect({"tool": "open_path", "arguments": {"path": os.path.join(HOME, "Documents", name)}}) == "irreversible"


@pytest.mark.parametrize("name", ["notes.txt", "game.uproject", "scene.blend", "readme.md", "report.pdf", "app.sln"])
def test_documents_and_projects_stay_ordinary_opens(name):
    assert not pcverbs.runs_code(name)
    assert authz.effect({"tool": "open_path", "arguments": {"path": os.path.join(HOME, "Documents", name)}}) == "reversible"


def test_open_path_refuses_a_script_without_the_owners_yes_and_a_t3_script_even_with_it(tmp_path, monkeypatch):
    from tools import software
    launched = []
    monkeypatch.setattr(software, "launch_as_user", lambda p: launched.append(p))
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    harmless = tmp_path / "hello.bat"
    harmless.write_text("@echo off\necho hello\n")
    with pytest.raises(RuntimeError, match="needs your yes"):
        pcverbs.open_path({"path": str(harmless)})
    evil = tmp_path / "quiet.cmd"
    evil.write_text("@echo off\nSet-MpPreference -DisableRealtimeMonitoring $true\n")
    assert pcverbs.script_tier(str(evil)) >= 3
    with pytest.raises(RuntimeError, match="never run"):
        pcverbs.open_path({"path": str(evil)}, approved=True)
    ps1 = tmp_path / "x.ps1"
    ps1.write_text("Write-Output hi\n")
    with pytest.raises(RuntimeError, match="don't open"):
        pcverbs.open_path({"path": str(ps1)}, approved=True)
    assert launched == [], "nothing was started"
    assert pcverbs.script_tier(str(harmless)) == 2, "even a harmless script asks: running code is never an ordinary open"


def test_a_found_program_is_judged_again_before_it_is_opened():
    """'find my game and launch it' was authorized while the path said <found>; a found .exe must not run unasked."""
    ran = []

    def execute(s, **kw):
        ran.append(s)
        if s["tool"] == "find_project":
            return {"verified": True, "detail": "found", "found": os.path.join(HOME, "Games", "Thing", "Thing.exe")}
        return {"verified": True, "detail": "opened"}
    plan = {"steps": [{"tool": "find_project", "arguments": {"name": "thing"}}, {"tool": "open_path", "arguments": {"path": "<found>"}}]}
    out = pcagent.pursue("find my thing game and launch it", plan, execute=execute, replan=lambda *a: None,
                         reauthorize=lambda st: authz.effect(st) not in (authz.IRREVERSIBLE, authz.EXTERNAL))
    assert out["state"] == "needs_review" and [s["tool"] for s in ran] == ["find_project"]
    assert out["proposed"]["steps"][0]["arguments"]["path"].endswith("Thing.exe")
    found_project = dict(plan)
    out = pcagent.pursue("find my game and launch it", found_project, replan=lambda *a: None,
                         execute=lambda s, **kw: {"verified": True, "detail": "x", **({"found": os.path.join(HOME, "G", "G.uproject")} if s["tool"] == "find_project" else {})},
                         reauthorize=lambda st: authz.effect(st) not in (authz.IRREVERSIBLE, authz.EXTERNAL))
    assert out["state"] == "completed", "a found .uproject is still an ordinary open"
