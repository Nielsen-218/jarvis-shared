"""A shell child started from PowerShell 7 must still be a working Windows PowerShell: pwsh's module folders are not for 5.1."""
import os
import sys

import pytest

from jarvis_pc.actions import shell

PWSH = (r"C:\Users\x\OneDrive\Documents\PowerShell\Modules;C:\Program Files\PowerShell\Modules;"
        r"c:\program files\windowsapps\microsoft.powershell_7.6.9.0_x64__8wekyb3d8bbwe\Modules;"
        r"C:\Program Files\WindowsPowerShell\Modules;C:\Windows\system32\WindowsPowerShell\v1.0\Modules;D:\MyModules")


def test_pwsh_module_folders_are_dropped_and_everything_else_kept():
    out = shell.windows_powershell_module_path(PWSH)
    assert out == r"C:\Program Files\WindowsPowerShell\Modules;C:\Windows\system32\WindowsPowerShell\v1.0\Modules;D:\MyModules"
    assert shell.windows_powershell_module_path("") == "" and shell.windows_powershell_module_path(None) is None


def test_a_callers_env_is_cleaned_not_replaced(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    env = shell.child_env({"PSModulePath": PWSH, "KEEP": "1"})
    assert env["KEEP"] == "1" and "microsoft.powershell_" not in env["PSModulePath"]
    upper = shell.child_env({"PSMODULEPATH": PWSH})                    # the key as a Windows os.environ copy spells it
    assert "microsoft.powershell_" not in upper["PSMODULEPATH"]
    monkeypatch.setattr(shell, "IS_WINDOWS", False)
    assert shell.child_env(None) is None, "other platforms are untouched"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows PowerShell only")
def test_get_filehash_exists_even_when_the_parent_is_pwsh(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("PSModulePath", PWSH.replace(r"C:\Users\x", os.path.expanduser("~")))
    f = tmp_path / "a.txt"
    f.write_text("hello\n")
    r = subprocess.run(shell.shell_argv(f"Get-FileHash -Path '{f}' -Algorithm SHA256 | Select-Object -ExpandProperty Hash"),
                       capture_output=True, text=True, env=shell.child_env(None))
    assert r.returncode == 0 and len(r.stdout.strip()) == 64, r.stderr[:300]
