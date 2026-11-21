import os
import shutil
import tempfile
import unittest
from unittest import mock

from jarvis_pc.files import FileTools, find_duplicates, find_files, sha256_of
from jarvis_pc.guardrails import Guardrails
from jarvis_pc.verify import ChangeJournal
from jarvis_pc.testing import module_guard

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.docs = os.path.join(self.tmp, "docs")
        self.bk = os.path.join(self.tmp, "backups")
        os.makedirs(self.docs)
        self.approvals = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text="x", root=None):
        p = os.path.normpath(os.path.join(root or self.docs, rel))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p

    def tools(self, approve=True, dry_run=False):
        def ask(req):
            self.approvals.append((req.tier.name, req.command))
            return approve
        g = Guardrails(allowlist_roots=[self.tmp], dry_run=dry_run, approver=ask)
        j = ChangeJournal(os.path.join(self.tmp, "j", "journal.jsonl"), os.path.join(self.tmp, "j", "s"))
        return FileTools(g, j), g, j


class SearchTests(Base):
    def test_find_files_glob_case_insensitive_and_limit(self):
        self.write("a/Report.PDF")
        self.write("b/c/notes.txt")
        self.write("b/other.pdf")
        got = find_files(self.docs, "*.pdf")
        self.assertEqual(sorted(os.path.basename(p).lower() for p in got), ["other.pdf", "report.pdf"])
        self.assertEqual(len(find_files(self.docs, "*", max_results=2)), 2)

    def test_credential_folders_are_never_searched(self):
        self.write(".ssh/id_rsa", "secret")
        self.write("ok.txt")
        names = [os.path.basename(p) for p in find_files(self.docs, "*")]
        self.assertEqual(names, ["ok.txt"])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_links_are_not_followed(self):
        outside = os.path.join(self.tmp, "outside")
        self.write("secret.txt", root=outside)
        try:
            os.symlink(outside, os.path.join(self.docs, "link"), target_is_directory=True)
        except OSError:
            self.skipTest("symlink not permitted")
        self.assertEqual(find_files(self.docs, "*"), [])


class DuplicateTests(Base):
    def test_groups_by_content_not_name_and_reports_waste(self):
        a = self.write("a.txt", "same content")
        b = self.write("sub/b.txt", "same content")
        self.write("c.txt", "different!!!!")      # same size as above (12 vs 13?) -> different content anyway
        self.write("d.txt", "same conten2")       # same size, different hash
        groups = find_duplicates(self.docs)
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0].paths), sorted([a, b]))
        self.assertEqual(groups[0].wasted, len("same content"))

    def test_report_only_never_deletes(self):
        a = self.write("a.txt", "dup")
        b = self.write("b.txt", "dup")
        find_duplicates(self.docs)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_empty_files_ignored_by_default(self):
        self.write("e1.txt", "")
        self.write("e2.txt", "")
        self.assertEqual(find_duplicates(self.docs), [])


class BackupTests(Base):
    def test_folder_backup_copies_verifies_and_journals(self):
        self.write("a.txt", "alpha")
        self.write("sub/b.txt", "beta")
        t, g, j = self.tools()
        r = t.backup(self.docs, self.bk)
        self.assertEqual(r.report.result, "PASS", r.report.render())
        self.assertTrue(os.path.isdir(r.dest))
        self.assertTrue(os.path.exists(os.path.join(self.docs, "a.txt")))  # original untouched
        self.assertEqual(sha256_of(os.path.join(r.dest, "sub", "b.txt")), sha256_of(os.path.join(self.docs, "sub", "b.txt")))
        self.assertEqual([tier for tier, _ in self.approvals], [])  # T1: reversible, no prompt
        self.assertEqual(len(j.entries()), 1)

    def test_single_file_keeps_its_extension(self):
        p = self.write("report.pdf", "pdfdata")
        t, g, j = self.tools()
        r = t.backup(p, self.bk)
        self.assertEqual(r.report.result, "PASS", r.report.render())
        self.assertTrue(r.dest.endswith(".pdf"))
        self.assertIn("report-backup-", os.path.basename(r.dest))

    def test_undo_removes_only_the_backup_after_approval(self):
        self.write("a.txt")
        t, g, j = self.tools()
        r = t.backup(self.docs, self.bk)
        out = j.undo_last_group(g)
        self.assertTrue(all(o.ok for o in out))
        self.assertFalse(os.path.exists(r.dest))
        self.assertTrue(os.path.exists(os.path.join(self.docs, "a.txt")))
        self.assertEqual([tier for tier, _ in self.approvals], ["T2"])  # deleting what we created still asks

    def test_never_overwrites_and_each_backup_is_new(self):
        self.write("a.txt")
        t, g, j = self.tools()
        t.clock = lambda: 1_700_000_000  # same second
        r1, r2 = t.backup(self.docs, self.bk), t.backup(self.docs, self.bk)
        self.assertNotEqual(r1.dest, r2.dest)
        self.assertTrue(os.path.isdir(r1.dest) and os.path.isdir(r2.dest))

    def test_blocked_outside_allowlist_and_missing_source_and_dry_run(self):
        self.write("a.txt")
        t, g, j = self.tools()
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            self.assertEqual(t.backup(self.docs, outside).report.result, "FAIL")
            self.assertEqual(os.listdir(outside), [])
        finally:
            shutil.rmtree(outside, ignore_errors=True)
        self.assertEqual(t.backup(os.path.join(self.tmp, "nope"), self.bk).report.result, "FAIL")
        t2, _, j2 = self.tools(dry_run=True)
        self.assertEqual(t2.backup(self.docs, self.bk).report.result, "FAIL")
        self.assertFalse(os.path.exists(self.bk))

    def test_backup_cannot_land_inside_its_own_source(self):
        self.write("a.txt")
        t, g, j = self.tools()
        r = t.backup(self.docs, os.path.join(self.docs, "backups"))
        self.assertEqual(r.report.result, "FAIL")
        self.assertIn("inside the source", r.report.render())

    def test_credential_folders_are_not_copied_and_not_a_source(self):
        self.write("ok.txt", "fine")
        self.write(".ssh/id_rsa", "secret")
        t, g, j = self.tools()
        r = t.backup(self.docs, self.bk)
        self.assertEqual(r.report.result, "PASS", r.report.render())
        self.assertFalse(os.path.exists(os.path.join(r.dest, ".ssh")))
        self.assertEqual(t.backup(os.path.join(self.docs, ".ssh"), self.bk).report.result, "FAIL")

    def test_corrupted_copy_is_caught_by_the_hash_check(self):
        self.write("a.txt", "original")
        t, g, j = self.tools()
        real_copytree = shutil.copytree

        def corrupting(src, dest, **kw):
            real_copytree(src, dest, **kw)
            with open(os.path.join(dest, "a.txt"), "w") as f:
                f.write("tampered")
        with mock.patch("jarvis_pc.files.ops.shutil.copytree", corrupting):
            r = t.backup(self.docs, self.bk)
        self.assertNotEqual(r.report.result, "PASS")
        self.assertTrue(any(not c.passed and "changed or missing" in c.detail for c in r.report.checks))

    def test_failed_copy_is_journaled_so_the_leftover_can_be_undone(self):
        self.write("a.txt")
        t, g, j = self.tools()

        def boom(src, dest, **kw):
            os.makedirs(dest)
            raise OSError("disk full")
        with mock.patch("jarvis_pc.files.ops.shutil.copytree", boom):
            r = t.backup(self.docs, self.bk)
        self.assertEqual(r.report.result, "FAIL")
        self.assertEqual(len(j.entries()), 1)  # the half-made folder is recorded, so it is not orphaned
        self.assertTrue(all(o.ok for o in j.undo_last_group(g)))
        self.assertFalse(os.path.exists(r.dest))


class RestoreTestTests(Base):
    def test_restore_test_passes_and_cleans_up(self):
        self.write("a.txt", "alpha")
        self.write("sub/b.txt", "beta")
        t, g, j = self.tools()
        b = t.backup(self.docs, self.bk)
        scratch = os.path.join(self.tmp, "scratch")
        rep = t.restore_test(b.dest, scratch)
        self.assertEqual(rep.result, "PASS", rep.render())
        self.assertEqual(len(os.listdir(scratch)), 1)
        self.assertTrue(all(o.ok for o in j.undo_last_group(g)))
        self.assertEqual(os.listdir(scratch), [])

    def test_restore_test_detects_a_rotted_backup_even_though_it_restores_identically(self):
        self.write("a.txt", "alpha")
        self.write("b.txt", "beta")
        t, g, j = self.tools()
        b = t.backup(self.docs, self.bk)
        with open(os.path.join(b.dest, "a.txt"), "w") as f:
            f.write("rotted")
        rep = t.restore_test(b.dest, os.path.join(self.tmp, "scratch"))
        self.assertEqual(rep.result, "PARTIAL", rep.render())  # file count fine, hash check fails
        self.assertTrue(any(not c.passed and "damaged" in c.detail for c in rep.checks))

    def test_missing_file_in_backup_is_detected(self):
        self.write("a.txt", "alpha")
        self.write("b.txt", "beta")
        t, g, j = self.tools()
        b = t.backup(self.docs, self.bk)
        os.remove(os.path.join(b.dest, "b.txt"))
        rep = t.restore_test(b.dest, os.path.join(self.tmp, "scratch"))
        self.assertEqual(rep.result, "FAIL", rep.render())

    def test_no_manifest_means_unproven(self):
        t, g, j = self.tools()
        rep = t.restore_test(self.docs, os.path.join(self.tmp, "scratch"))
        self.assertEqual(rep.result, "FAIL")
        self.assertIn("integrity cannot be proven", rep.render())

    def test_single_file_backup_restore_test(self):
        p = self.write("one.txt", "solo")
        t, g, j = self.tools()
        b = t.backup(p, self.bk)
        rep = t.restore_test(b.dest, os.path.join(self.tmp, "scratch"))
        self.assertEqual(rep.result, "PASS", rep.render())

    def test_restore_outside_allowlist_blocked(self):
        self.write("a.txt")
        t, g, j = self.tools()
        b = t.backup(self.docs, self.bk)
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            self.assertEqual(t.restore_test(b.dest, outside).result, "FAIL")
            self.assertEqual(os.listdir(outside), [])
        finally:
            shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
