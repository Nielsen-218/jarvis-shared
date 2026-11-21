"""The test-time safety net itself: it must really block, and must not get in the way of legitimate temp-folder work."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from jarvis_pc.guardrails import Guardrails
from jarvis_pc.learning import PlaybookStore
from jarvis_pc.testing import UnsafeTestAction, _inside_temp, module_guard

setUpModule, tearDownModule = module_guard()


class SafetyNetTests(unittest.TestCase):
    def test_real_winget_uninstall_and_friends_cannot_launch(self):
        for cmd in ("winget uninstall --id Git.Git --exact --silent", "winget install --id jqlang.jq",
                    ["winget", "upgrade", "--all"], "msiexec /x {GUID} /qn", "powershell -Command Remove-Item C:\\x -Recurse",
                    "schtasks /delete /tn x", "reg delete HKCU\\Software\\X /f", "powershell Set-MpPreference -DisableRealtimeMonitoring 1"):
            with self.assertRaises(UnsafeTestAction, msg=str(cmd)):
                subprocess.run(cmd, shell=isinstance(cmd, str))

    def test_reading_the_uninstall_registry_key_is_allowed_but_uninstalling_is_not(self):
        from jarvis_pc.testing import RISKY_COMMAND
        self.assertIsNone(RISKY_COMMAND.search(r"Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\* | Select-Object DisplayName"))
        for bad in ("winget uninstall --id X", "powershell uninstall-package foo", "uninstall.exe /S", "msiexec /x {G}"):
            self.assertIsNotNone(RISKY_COMMAND.search(bad), bad)

    def test_through_the_real_gated_runner_even_if_the_gate_were_broken(self):
        """The one test that uses the real runner with winget text (the gate blocks it in dry-run). If the gate ever let
        it through, the safety net still stops the real process from starting."""
        from jarvis_pc.actions.shell import run
        g = Guardrails(allowlist_roots=[tempfile.gettempdir()], dry_run=False, approver=lambda r: True)  # gate says yes
        with self.assertRaises(UnsafeTestAction):
            run(g, "winget install --id jqlang.jq --exact --silent")

    def test_deleting_outside_temp_is_refused_and_inside_temp_still_works(self):
        # a path that is certainly NOT under the temp folder (the project itself may live under temp, so do not use it)
        outside = os.path.join(os.path.expanduser("~"), "does_not_exist_zzz")
        if _inside_temp(outside):
            self.skipTest("home folder is inside the temp folder on this machine")
        with self.assertRaises(UnsafeTestAction):
            shutil.rmtree(outside)
        with self.assertRaises(UnsafeTestAction):
            os.remove(outside + ".txt")
        self.assertFalse(os.path.exists(outside))
        d = tempfile.mkdtemp()
        f = os.path.join(d, "x.txt")
        open(f, "w").write("x")
        os.remove(f)
        shutil.rmtree(d)
        self.assertFalse(os.path.exists(d))

    def test_legitimate_read_only_and_temp_work_is_unaffected(self):
        out = subprocess.run([sys.executable, "--version"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        d = tempfile.mkdtemp()
        PlaybookStore(os.path.join(d, "pb.jsonl")).all()
        shutil.rmtree(d)

    def test_the_net_is_removed_after_the_module(self):
        tearDownModule()   # leave the module guard (depth 1 -> 0) ...
        try:
            self.assertEqual(subprocess.run([sys.executable, "-c", "print('winget')"], capture_output=True, text=True).returncode, 0)
        finally:
            setUpModule()  # ... and restore it so the rest of this module is still protected


if __name__ == "__main__":
    unittest.main()
