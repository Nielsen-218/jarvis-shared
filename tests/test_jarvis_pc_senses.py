import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from jarvis_pc.devenv import backup_path, tool_versions
from jarvis_pc.guardrails import Guardrails, Tier
from jarvis_pc.network import check_targets
from jarvis_pc.perception import foreground, screenshot, ui_tree, uia_available, windows
from jarvis_pc.verify import ChangeJournal, CheckResult
from jarvis_pc.voice import VoiceApprover, parse_confirmation
from jarvis_pc.testing import module_guard

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests


def shell(table, ran=True):
    def run_fn(guard, command, **kw):
        d = SimpleNamespace(reasons=["T2: blocked"], id="d", tier=Tier.T0)
        if not ran:
            return SimpleNamespace(ran=False, ok=False, decision=d, stdout="", stderr="", exit_code=None, error_kind=None, truncated=False)
        v = table.get(command)
        if v is None:
            return SimpleNamespace(ran=True, ok=False, decision=d, stdout="", stderr="not recognized", exit_code=1,
                                   error_kind="not_found", truncated=False)
        text = v if isinstance(v, str) else json.dumps(v)
        return SimpleNamespace(ran=True, ok=True, decision=d, stdout=text, stderr="", exit_code=0, error_kind=None, truncated=False)
    return run_fn


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.guard = Guardrails(allowlist_roots=[self.tmp], dry_run=False)
        self.journal = ChangeJournal(os.path.join(self.tmp, "j", "journal.jsonl"), os.path.join(self.tmp, "j", "s"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class PerceptionTests(Base):
    def test_windows_keeps_only_titled_windows_and_handles_one_row(self):
        from jarvis_pc.perception.core import CMD_WINDOWS
        rows = [{"Name": "chrome", "Id": 1, "MainWindowTitle": "GitHub - Chrome"}, {"Name": "svchost", "Id": 2, "MainWindowTitle": ""}]
        got = windows(self.guard, shell({CMD_WINDOWS: rows}))
        self.assertEqual([(w.title, w.process) for w in got], [("GitHub - Chrome", "chrome")])
        one = windows(self.guard, shell({CMD_WINDOWS: rows[0]}))
        self.assertEqual(len(one), 1)

    def test_windows_failure_is_an_error_not_an_empty_list(self):
        with self.assertRaises(RuntimeError):
            windows(self.guard, shell({}, ran=False))

    def test_foreground_with_fake_user32(self):
        class U:
            def GetForegroundWindow(self): return 42
            def GetWindowTextLengthW(self, h): return 5
            def GetWindowTextW(self, h, buf, n): buf.value = "Hello"
            def GetWindowThreadProcessId(self, h, ref): ref._obj.value = 777
        w = foreground(U())
        self.assertEqual((w.title, w.pid), ("Hello", 777))

        class NoWindow(U):
            def GetForegroundWindow(self): return 0
        self.assertIsNone(foreground(NoWindow()))

    def test_ui_tree_with_fake_module_and_honest_when_missing(self):
        class Ctrl:
            def __init__(self, name, kind, kids=()): self.Name, self.ControlTypeName, self._k = name, kind, list(kids)
            def GetChildren(self): return self._k
            def Exists(self, a, b): return True
        win = Ctrl("Notepad", "WindowControl", [Ctrl("Save", "ButtonControl"), Ctrl("", "PaneControl", [Ctrl("Text", "EditControl")])])
        auto = SimpleNamespace(WindowControl=lambda **kw: win)
        lines = ui_tree("Notepad", module=auto)
        self.assertEqual(lines[0], "WindowControl: Notepad")
        self.assertIn("  ButtonControl: Save", lines)
        self.assertTrue(uia_available(auto))
        gone = SimpleNamespace(WindowControl=lambda **kw: SimpleNamespace(Exists=lambda a, b: False))
        self.assertEqual(ui_tree("x", module=gone), [])

    def test_ui_tree_limits_output(self):
        class Ctrl:
            def __init__(self, n): self.Name, self.ControlTypeName = f"c{n}", "X"
            def GetChildren(self): return [Ctrl(i) for i in range(50)] if self.Name == "root" else []
            def Exists(self, a, b): return True
        root = Ctrl(0); root.Name = "root"
        out = ui_tree("x", module=SimpleNamespace(WindowControl=lambda **kw: root), max_items=10)
        self.assertEqual(len(out), 10)

    def test_screenshot_writes_verifies_and_undoes(self):
        from PIL import Image
        r = screenshot(self.guard, self.journal, os.path.join(self.tmp, "shots"), grabber=lambda: Image.new("RGB", (8, 6), "red"))
        self.assertEqual(r.result, "PASS", r.render())
        png = [f for f in os.listdir(os.path.join(self.tmp, "shots"))][0]
        self.assertTrue(png.endswith(".png"))
        g = Guardrails(allowlist_roots=[self.tmp], dry_run=False, approver=lambda q: True)
        self.assertTrue(all(o.ok for o in self.journal.undo_last_group(g)))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "shots")), [])

    def test_screenshot_blocked_outside_allowlist_dry_run_and_capture_failure(self):
        from PIL import Image
        grab = lambda: Image.new("RGB", (2, 2))  # noqa: E731
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            self.assertEqual(screenshot(self.guard, self.journal, outside, grabber=grab).result, "FAIL")
            self.assertEqual(os.listdir(outside), [])
        finally:
            shutil.rmtree(outside, ignore_errors=True)
        dry = Guardrails(allowlist_roots=[self.tmp], dry_run=True)
        self.assertEqual(screenshot(dry, self.journal, self.tmp, grabber=grab).result, "FAIL")

        def boom():
            raise OSError("no display")
        self.assertEqual(screenshot(self.guard, self.journal, self.tmp, grabber=boom).result, "FAIL")


class DevenvNetworkTests(Base):
    def test_tool_versions(self):
        fs = tool_versions(self.guard, shell({"git --version": "git version 2.55"}), names=["git", "node"])
        self.assertEqual(fs[0].severity, "ok")
        self.assertIn("2.55", fs[0].detail)
        self.assertEqual(fs[1].severity, "info")  # not installed is information, with a next step
        self.assertTrue(fs[1].hint)
        self.assertTrue(tool_versions(self.guard, shell({}, ran=False), names=["git"])[0].check_error)
        self.assertTrue(tool_versions(self.guard, shell({}), names=["cobol"])[0].check_error)

    def test_every_probe_is_t0(self):
        from jarvis_pc.devenv import PROBES
        from jarvis_pc.guardrails import classify
        for cmd in PROBES.values():
            self.assertEqual(classify(cmd, None, self.guard.allowlist).tier, Tier.T0, cmd)

    def test_path_backup_roundtrip_and_blocks(self):
        vals = {"user": r"C:\A;C:\B", "machine": r"C:\C"}
        r = backup_path(self.guard, self.journal, os.path.join(self.tmp, "envb"), lambda: vals)
        self.assertEqual(r.result, "PASS", r.render())
        f = os.path.join(self.tmp, "envb", os.listdir(os.path.join(self.tmp, "envb"))[0])
        self.assertEqual(json.load(open(f, encoding="utf-8")), vals)
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            self.assertEqual(backup_path(self.guard, self.journal, outside, lambda: vals).result, "FAIL")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_network_targets(self):
        def fake(host, port, timeout=1):
            return CheckResult("p", host == "up", "ok" if host == "up" else "refused")
        fs = check_targets([("Laptop", "up", 8000), ("Pi", "down", 22)], check=fake)
        self.assertEqual([f.severity for f in fs], ["ok", "warning"])
        self.assertIn("down:22", fs[1].detail)


class VoiceTests(unittest.TestCase):
    def test_clear_yes_no_and_adversarial_ambiguity(self):
        for t in ("yes", "Yes.", "yeah", "Jarvis, yes please", "go ahead", "OK", "confirm", "oui"):
            self.assertTrue(parse_confirmation(t), t)
        for t in ("no", "No!", "stop", "cancel", "don't", "never mind no", "wait"):
            self.assertIs(parse_confirmation(t), False, t)
        for t in (None, "", "  ", "maybe", "uh", "yes but wait", "yes no", "I guess so", "what", "yesterday", "nope yes"):
            self.assertIsNone(parse_confirmation(t), t)

    def req(self, cmd="install foo"):
        return SimpleNamespace(command=cmd, reasons=["method: release-zip"])

    def test_approver_asks_then_obeys(self):
        said = []
        ap = VoiceApprover(said.append, lambda: "yes")
        self.assertTrue(ap(self.req()))
        self.assertIn("install foo", said[0])
        self.assertFalse(VoiceApprover(said.append, lambda: "no")(self.req()))

    def test_unclear_then_clear_and_silence_is_a_no(self):
        replies = iter(["hmm", "yes"])
        said = []
        self.assertTrue(VoiceApprover(said.append, lambda: next(replies))(self.req()))
        self.assertTrue(any("clear yes or no" in s for s in said))
        silent = VoiceApprover(lambda s: None, lambda: None)
        self.assertFalse(silent(self.req()))
        self.assertEqual(silent.log[-1][2], False)

    def test_listener_crash_is_a_no(self):
        def boom():
            raise RuntimeError("mic unplugged")
        self.assertFalse(VoiceApprover(lambda s: None, boom)(self.req()))

    def test_plugs_into_the_gate(self):
        g = Guardrails(allowlist_roots=[tempfile.gettempdir()], dry_run=False, approver=VoiceApprover(lambda s: None, lambda: "no"))
        d = g.authorize_action("install x", tier=Tier.T2, reasons=["test"])
        self.assertFalse(d.allowed)
        g2 = Guardrails(allowlist_roots=[tempfile.gettempdir()], dry_run=False, approver=VoiceApprover(lambda s: None, lambda: "yes"))
        self.assertTrue(g2.authorize_action("install x", tier=Tier.T2, reasons=["test"]).allowed)


if __name__ == "__main__":
    unittest.main()
