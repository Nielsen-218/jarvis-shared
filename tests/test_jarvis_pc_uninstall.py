import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from jarvis_pc.guardrails import Guardrails
from jarvis_pc.uninstall import UninstallPipeline
from jarvis_pc.verify import ChangeJournal
from jarvis_pc.testing import module_guard

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.apps = os.path.join(self.tmp, "apps")
        self.q = os.path.join(self.tmp, "quarantine")
        os.makedirs(os.path.join(self.apps, "_downloads"))
        self.app = self.make_app("ripgrep-15.2.0", 3)
        self.zip = os.path.join(self.apps, "_downloads", "ripgrep-15.2.0-x86_64.zip")
        self.write(self.zip, "zipdata")
        self.prompts = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, p, text="x"):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def make_app(self, name, n):
        d = os.path.join(self.apps, name)
        for i in range(n):
            self.write(os.path.join(d, f"f{i}.txt"))
        return d

    def build(self, approve=True, dry_run=False, run_fn=None):
        def ask(req):
            self.prompts.append((req.tier.name, req.command))
            return approve
        g = Guardrails(allowlist_roots=[self.tmp], dry_run=dry_run, approver=ask,
                       audit_path=os.path.join(self.tmp, "audit.jsonl"))
        j = ChangeJournal(os.path.join(self.tmp, "j", "journal.jsonl"), os.path.join(self.tmp, "j", "snaps"))
        kw = {"run_fn": run_fn} if run_fn else {}
        return UninstallPipeline(g, j, apps_dir=self.apps, quarantine_dir=self.q, **kw), g, j


class UninstallFolderTests(Base):
    def test_uninstall_moves_app_and_installer_to_quarantine_and_verifies(self):
        p, g, j = self.build()
        plan = p.plan_uninstall("ripgrep")
        self.assertEqual(len(plan.moves), 2)  # the folder and its zip
        res = p.execute(plan)
        self.assertEqual(res.report.result, "PASS", res.report.render())
        self.assertFalse(os.path.exists(self.app))
        self.assertFalse(os.path.exists(self.zip))
        self.assertEqual(sum(len(f) for _, _, f in os.walk(self.q)), 4)  # nothing deleted, all kept
        self.assertEqual(g.audit.verify(), (True, None))
        self.assertEqual(self.prompts[0][0], "T2")  # the plan was asked about

    def test_undo_last_group_puts_everything_back(self):
        p, g, j = self.build()
        p.execute(p.plan_uninstall("ripgrep"))
        out = j.undo_last_group(g)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(o.ok for o in out))
        self.assertEqual(len(os.listdir(self.app)), 3)
        self.assertTrue(os.path.isfile(self.zip))
        self.assertFalse(os.path.exists(self.q))  # empty quarantine batch folders were tidied away
        self.assertTrue(os.path.isdir(self.tmp))  # but never the allowlist root

    def test_denied_approval_changes_nothing(self):
        p, g, j = self.build(approve=False)
        res = p.execute(p.plan_uninstall("ripgrep"))
        self.assertEqual(res.report.result, "FAIL")
        self.assertTrue(os.path.isdir(self.app))
        self.assertTrue(os.path.isfile(self.zip))
        self.assertEqual(j.entries(), [])

    def test_dry_run_changes_nothing(self):
        p, g, j = self.build(dry_run=True)
        res = p.execute(p.plan_uninstall("ripgrep"))
        self.assertFalse(res.executed)
        self.assertTrue(os.path.isdir(self.app))

    def test_refuses_path_tricks_and_protected_names(self):
        p, g, j = self.build()
        for bad in ("..", "../apps", "a/b", "_downloads", "C:\\Windows", "", "ripgrep;rm"):
            self.assertTrue(p.plan_uninstall(bad).blocked_reason, bad)
        self.assertTrue(p.plan_uninstall("nothing-here").blocked_reason)

    def test_ambiguous_name_is_blocked(self):
        self.make_app("ripgrep-14.0.0", 1)
        p, g, j = self.build()
        self.assertIn("more than one match", p.plan_uninstall("ripgrep").blocked_reason)
        self.assertFalse(p.plan_uninstall("ripgrep-14.0.0").blocked_reason)

    def test_failure_midway_rolls_back_the_moves(self):
        p, g, j = self.build()
        plan = p.plan_uninstall("ripgrep")
        os.remove(self.zip)  # installer vanishes after planning: second move fails
        res = p.execute(plan)
        self.assertTrue(res.rolled_back)
        self.assertEqual(len(os.listdir(self.app)), 3)
        self.assertEqual(res.pending_undo, [])

    def test_outside_allowlist_is_never_touched(self):
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            g = Guardrails(allowlist_roots=[self.apps, self.q], dry_run=False, approver=lambda r: True)
            j = ChangeJournal(os.path.join(self.apps, "j.jsonl"), os.path.join(self.apps, "snaps"))
            p = UninstallPipeline(g, j, apps_dir=outside, quarantine_dir=self.q)
            os.makedirs(os.path.join(outside, "tool-1"))
            self.assertTrue(p.plan_uninstall("tool").blocked_reason)
            self.assertTrue(os.path.isdir(os.path.join(outside, "tool-1")))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_links_are_not_followed(self):
        target = os.path.join(self.tmp, "precious")
        self.write(os.path.join(target, "keep.txt"))
        link = os.path.join(self.apps, "evil-1")
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            self.skipTest("symlink not permitted")
        p, g, j = self.build()
        self.assertTrue(p.plan_uninstall("evil").blocked_reason)
        self.assertTrue(os.path.isfile(os.path.join(target, "keep.txt")))

    def test_undo_will_not_overwrite_a_reinstalled_app(self):
        p, g, j = self.build()
        p.execute(p.plan_uninstall("ripgrep"))
        self.make_app("ripgrep-15.2.0", 1)  # user reinstalled meanwhile
        out = j.undo_last_group(g)
        self.assertFalse(all(o.ok for o in out))
        self.assertEqual(len(os.listdir(self.app)), 1)  # new copy untouched


class CleanupAndPurgeTests(Base):
    def test_cleanup_only_takes_leftovers_not_wanted_installers(self):
        part = os.path.join(self.apps, "_downloads", "big.zip.part")
        keep = os.path.join(self.apps, "_downloads", "unrelated-tool.zip")
        empty = os.path.join(self.apps, "empty-dir")
        self.write(part)
        self.write(keep)
        os.makedirs(empty)
        p, g, j = self.build()
        plan = p.plan_cleanup()
        self.assertEqual({m.src for m in plan.moves}, {part, self.zip, empty})
        res = p.execute(plan)
        self.assertEqual(res.report.result, "PASS", res.report.render())
        self.assertTrue(os.path.isfile(keep))  # unmatched installer left alone
        self.assertTrue(os.path.isdir(self.app))  # the installed app is never a cleanup target
        self.assertTrue(all(o.ok for o in j.undo_last_group(g)))
        self.assertTrue(os.path.isfile(part) and os.path.isfile(self.zip) and os.path.isdir(empty))

    def test_age_zero_means_any_age_even_if_mtime_is_slightly_in_the_future(self):
        # Regression: on Windows a new file's mtime can sit ahead of time.time(); that made brand-new items look
        # "too young" and randomly dropped them from cleanup and purge plans.
        fut = time.time() + 30
        os.utime(self.zip, (fut, fut))
        p, g, j = self.build()
        self.assertEqual([m.src for m in p.plan_cleanup(min_age_days=0).moves], [self.zip])
        p.execute(p.plan_uninstall("ripgrep"))
        batch = os.path.join(self.q, os.listdir(self.q)[0])
        os.utime(batch, (fut, fut))
        self.assertEqual(len(p.plan_purge(older_than_days=0).purge), 1)
        self.assertTrue(p.plan_purge(older_than_days=7).empty)  # a real age floor still applies

    def test_cleanup_respects_min_age(self):
        p, g, j = self.build()
        self.assertTrue(p.plan_cleanup(min_age_days=30).empty)
        old = time.time() - 40 * 86400
        os.utime(self.zip, (old, old))
        self.assertEqual([m.src for m in p.plan_cleanup(min_age_days=30).moves], [self.zip])

    def test_purge_deletes_after_approval_and_cannot_be_undone(self):
        p, g, j = self.build()
        p.execute(p.plan_uninstall("ripgrep"))
        self.prompts.clear()
        res = p.execute(p.plan_purge())
        self.assertEqual(res.report.result, "PASS", res.report.render())
        self.assertEqual(os.listdir(self.q), [])
        self.assertEqual([t for t, _ in self.prompts], ["T2"])
        out = j.undo_last_group(g)  # the quarantined copy is gone, so undo reports it instead of faking success
        self.assertFalse(any(o.ok for o in out))
        self.assertIn("missing", out[0].detail)
        events = [ln for ln in open(j.path, encoding="utf-8") if '"type": "event"' in ln]
        self.assertEqual(len(events), 1)

    def test_purge_denied_keeps_everything(self):
        p, g, j = self.build()
        p.execute(p.plan_uninstall("ripgrep"))
        g.approver = lambda r: False
        res = p.execute(p.plan_purge())
        self.assertNotEqual(res.report.result, "PASS")
        self.assertEqual(len(os.listdir(self.q)), 1)

    def test_purge_never_leaves_quarantine(self):
        p, g, j = self.build()
        p.execute(p.plan_uninstall("ripgrep"))
        plan = p.plan_purge()
        plan.purge[0].src = self.apps  # a tampered plan pointing at the apps folder
        res = p.execute(plan)
        self.assertEqual(res.report.result, "FAIL")
        self.assertTrue(os.path.isdir(self.apps))
        self.assertEqual(len(os.listdir(self.apps)), 1)  # _downloads still there

    def test_purge_older_than_keeps_recent(self):
        p, g, j = self.build()
        p.execute(p.plan_uninstall("ripgrep"))
        self.assertTrue(p.plan_purge(older_than_days=7).empty)


class WingetTests(Base):
    @staticmethod
    def fake_run(state, removes=True):
        def run_fn(guard, command, **kw):
            d = SimpleNamespace(reasons=[], id="d", tier=SimpleNamespace(name="T1"))
            if command.startswith("winget list"):
                ok = state["installed"]
                return SimpleNamespace(ran=True, ok=ok, stdout="Git.Git 2.1" if ok else "No installed package found",
                                       stderr="", decision=d, exit_code=0 if ok else 1, error_kind=None)
            if command.startswith("winget uninstall") and removes:
                state["installed"] = False
            return SimpleNamespace(ran=True, ok=True, stdout="", stderr="", decision=d, exit_code=0, error_kind=None)
        return run_fn

    def test_winget_uninstall_verified_and_undo_is_a_reinstall(self):
        p, g, j = self.build(run_fn=self.fake_run({"installed": True}))
        plan = p.plan_winget("Git.Git")
        self.assertEqual(plan.winget_id, "Git.Git")
        res = p.execute(plan)
        self.assertEqual(res.report.result, "PASS", res.report.render())
        self.assertIn("winget install --id Git.Git", j.entries()[-1]["undo"][0]["command"])

    def test_winget_not_installed_or_bad_id_is_blocked(self):
        p, g, j = self.build(run_fn=self.fake_run({"installed": False}))
        self.assertTrue(p.plan_winget("Git.Git").blocked_reason)
        self.assertTrue(p.plan_winget("Git.Git; calc").blocked_reason)

    def test_still_installed_after_uninstall_fails_verification(self):
        p, g, j = self.build(run_fn=self.fake_run({"installed": True}, removes=False))
        # The rollback runs the journal's undo command (a winget reinstall). Never let a unit test reach the real shell.
        with mock.patch("jarvis_pc.verify.journal.run", side_effect=AssertionError("tests must not run real commands")) as fake_shell:
            fake_shell.side_effect = lambda guard, command, **kw: SimpleNamespace(
                ran=True, ok=True, exit_code=0, decision=SimpleNamespace(reasons=[]))
            res = p.execute(p.plan_winget("Git.Git"))
        self.assertIn("winget install --id Git.Git", fake_shell.call_args[0][1])
        self.assertNotEqual(res.report.result, "PASS")
        self.assertTrue(any("still installed" in c.detail for c in res.report.checks))


if __name__ == "__main__":
    unittest.main()
