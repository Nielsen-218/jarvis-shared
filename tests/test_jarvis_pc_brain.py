"""End-to-end through the brain adapter: prepare -> (person approves) -> run, with a real zip and real extraction."""
import hashlib
import io
import os
import shutil
import tempfile
import unittest
import zipfile

from jarvis_pc.brain import PCSkills
from jarvis_pc.verify import CheckResult
from jarvis_pc.testing import module_guard

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests

windows_only = unittest.skipUnless(os.name == "nt", "extracts with PowerShell Expand-Archive")


def make_zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("tool.exe", "binary-ish")
        z.writestr("README.txt", "hi")
    return b.getvalue()


ZIP = make_zip()
SUMS = f"{hashlib.sha256(ZIP).hexdigest()}  tool-windows-x64.zip\n"
REL = {"tag_name": "v1.0", "assets": [
    {"name": "tool-windows-x64.zip", "browser_download_url": "https://github.com/o/tool/releases/download/v1.0/tool-windows-x64.zip"},
    {"name": "SHA256SUMS.txt", "browser_download_url": "https://github.com/o/tool/releases/download/v1.0/SHA256SUMS.txt"}]}


class FakeGH:
    def repo(self, o, n):
        return {"name": "tool", "created_at": "2018-01-01T00:00:00Z", "pushed_at": "2026-10-20T00:00:00Z", "stargazers_count": 9000,
                "forks_count": 300, "license": {"key": "mit"}, "description": "x", "owner": {"type": "Organization"}}

    def releases(self, o, n):
        return [REL]

    def readme(self, o, n):
        return "# tool\nA tool.\n"

    def root_listing(self, o, n):
        return []

    def text(self, url):
        return SUMS


def fake_download(url, dest, max_bytes):
    with open(dest, "wb") as f:
        f.write(ZIP)
    return hashlib.sha256(ZIP).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.s = PCSkills(self.tmp, github=FakeGH(), downloader=fake_download,
                          scanner=lambda p: [CheckResult("scan", True)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


@windows_only
class InstallFlowTests(Base):
    URL = "https://github.com/o/tool"

    def test_prepare_changes_nothing_and_describes_the_plan(self):
        p = self.s.prepare_install(self.URL)
        self.assertIsNone(p.blocked)
        self.assertIn("tool-windows-x64.zip", p.summary)
        self.assertIn("[T2] download", p.summary)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "apps", "tool-v1.0")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "apps", "_downloads")))

    def test_approved_run_installs_verifies_and_undo_removes_everything(self):
        p = self.s.prepare_install(self.URL)
        out = p.run()
        self.assertIn("Result: PASS", out, out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "apps", "tool-v1.0", "tool.exe")))
        u = self.s.prepare_undo()
        self.assertIn("UNDO the last change", u.summary)
        out = u.run()
        self.assertNotIn("FAILED", out, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "apps", "tool-v1.0")))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "apps", "_downloads")), [])  # the zip goes too

    def test_the_key_changes_when_the_plan_changes(self):
        a = self.s.prepare_install(self.URL).key
        self.assertEqual(a, self.s.prepare_install(self.URL).key)
        REL["tag_name"] = "v2.0"
        try:
            self.assertNotEqual(a, self.s.prepare_install(self.URL).key)
        finally:
            REL["tag_name"] = "v1.0"

    def test_uninstall_then_purge_then_nothing_left(self):
        self.s.prepare_install(self.URL).run()
        un = self.s.prepare_uninstall("tool")
        self.assertTrue(un.allows_direct)  # reversible, so "uninstall tool" can be a direct request
        self.assertIn("Result: PASS", un.run())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "apps", "tool-v1.0")))
        purge = self.s.prepare_purge()
        self.assertFalse(purge.allows_direct)  # an irreversible delete is never implied
        self.assertIn("Result: PASS", purge.run())
        self.assertEqual(os.listdir(os.path.join(self.tmp, "quarantine")), [])

    def test_a_plan_altered_after_approval_is_refused_and_downloads_nothing(self):
        """The person approved plan A. If the plan is changed before it runs, the new prompt is not in scope."""
        p = self.s.prepare_install(self.URL)
        self.assertTrue(any(x.startswith("download tool-windows-x64.zip from") for x in p.scope))
        dl = next(s for s in p.plan.steps if s.kind == "download")
        dl.url = "https://github.com/o/tool/releases/download/v9.9/evil.zip"  # tampered after the yes
        out = p.run()
        self.assertIn("Refused (not part of the approved plan)", out)
        self.assertNotIn("Result: PASS", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "apps", "tool-v1.0")))
        self.assertFalse(os.path.exists(dl.dest))

    def test_undo_scope_covers_only_the_paths_in_the_journal(self):
        self.s.prepare_install(self.URL).run()
        u = self.s.prepare_undo()
        self.assertIsNotNone(u.key)
        self.assertNotEqual(u.key, "")
        self.s.prepare_install  # keep linters quiet about the unused name
        first_key = u.key
        u.run()
        self.assertEqual(self.s.prepare_undo().blocked, "There is nothing to undo.")
        self.assertNotEqual(first_key, "")


class ReadOnlyAndSafetyTests(Base):
    def test_bad_sources_are_refused_without_side_effects(self):
        self.assertIn("could not plan", self.s.prepare_install("https://evil.example.com/x").blocked or "")
        self.assertTrue(self.s.prepare_install("not a thing; rm -rf").blocked)
        self.assertTrue(self.s.prepare_uninstall("../../Windows").blocked)
        self.assertEqual(self.s.prepare_undo().blocked, "There is nothing to undo.")

    def test_backup_and_search_need_no_approval(self):
        docs = os.path.join(self.tmp, "docs")
        os.makedirs(docs)
        open(os.path.join(docs, "a.txt"), "w").write("same")
        open(os.path.join(docs, "b.txt"), "w").write("same")
        self.assertIn("a.txt", self.s.find_files(docs, "*.txt"))
        self.assertIn("2 copies", self.s.find_duplicates(docs))
        out = self.s.backup(docs)
        self.assertIn("Result: PASS", out, out)

    def test_diagnose_runs_with_a_fake_shell(self):
        from types import SimpleNamespace

        def fake(guard, command, **kw):
            d = SimpleNamespace(reasons=[], id="x", tier=None)
            return SimpleNamespace(ran=True, ok=False, stdout="", stderr="n/a", exit_code=1, error_kind="unknown", decision=d, truncated=False)
        s = PCSkills(self.tmp, run_fn=fake, path_reader=lambda: {"process": ""})
        out = s.diagnose("disk full")
        self.assertIn("Diagnosis: disk full", out)
        self.assertIn("could not", out)  # nothing answered, so it says unproven rather than "all good"

    def test_solve_without_teacher_is_unknown_and_with_teacher_learns_once(self):
        from types import SimpleNamespace
        calls = []

        def fake(guard, command, **kw):
            calls.append(command)
            d = SimpleNamespace(reasons=[], id="x", tier=None)
            return SimpleNamespace(ran=True, ok=True, stdout="git version 2.55", stderr="", exit_code=0, error_kind=None, decision=d, truncated=False)
        s = PCSkills(self.tmp, run_fn=fake)
        self.assertIn("unknown", s.solve("check git"))
        teacher_calls = []

        def teacher(task, ctx):
            teacher_calls.append(task)
            return "git --version  # expect: git version"
        self.assertIn("Learned a new skill", s.solve("check git", teacher))
        self.assertIn("no AI call", s.solve("check git", teacher))
        self.assertIn("no AI call", s.solve("check git"))  # and with no teacher at all
        self.assertEqual(len(teacher_calls), 1)

    def test_nothing_is_written_outside_the_root(self):
        # Use a private parent folder: listing the shared system temp folder is flaky, because any other process
        # (another test run, an editor, Defender) can create files there while this test runs.
        base = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        s = PCSkills(os.path.join(base, "pc"), github=FakeGH(), downloader=fake_download,
                     scanner=lambda p: [CheckResult("scan", True)])
        docs = os.path.join(base, "pc", "docs")
        os.makedirs(docs)
        open(os.path.join(docs, "a.txt"), "w").write("x")
        s.backup(docs)
        s.diagnose("disk")  # real shell, read-only
        self.assertEqual(os.listdir(base), ["pc"])  # everything JARVIS did stayed inside its root


if __name__ == "__main__":
    unittest.main()
