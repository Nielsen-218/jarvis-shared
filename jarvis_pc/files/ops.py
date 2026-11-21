"""File skills: search, duplicate report, and copy-then-verify backup. Same gate, journal and verify pattern.

- find_files / find_duplicates only READ. Reports never delete; removing a duplicate is a separate T2 step.
- backup() copies, then checks size + SHA-256 of every file against its source, and refuses to overwrite
  anything. Credential folders (.ssh, .aws, ...) are never copied. The copy is journaled with a delete-created undo, so the existing journal/undo rules apply
  (deleting what JARVIS created still needs approval).
- restore_test() proves a backup is restorable by restoring into a fresh scratch folder and comparing hashes.
"""
from __future__ import annotations

from ..text import plural

import fnmatch
import hashlib
import json
import os
import shutil
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..guardrails.gate import Guardrails
from ..guardrails.tiers import Tier
from ..verify.checks import CheckResult
from ..verify.journal import ChangeJournal, UndoStep
from ..verify.report import Report

PROTECTED_NAMES = {".ssh", ".gnupg", ".aws", ".azure", "login data", "cookies", "web data", ".kube"}


def _is_link(p: str) -> bool:
    isj = getattr(os.path, "isjunction", lambda x: False)
    return os.path.islink(p) or isj(p)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(root: str, *, max_files: int = 200_000, skip_names: Sequence[str] = ()):
    """Yield regular files under root. Never follows links; skips credential folders; stops at max_files."""
    count = 0
    stack = [root]
    skip = {n.lower() for n in skip_names} | PROTECTED_NAMES
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.name.lower() in skip or _is_link(e.path):
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append(e.path)
            elif e.is_file(follow_symlinks=False):
                count += 1
                if count > max_files:
                    return
                yield e.path


def find_files(root: str, pattern: str = "*", *, max_results: int = 200, max_files: int = 200_000) -> List[str]:
    """T0: file-name search (case-insensitive glob such as '*.pdf')."""
    pat = pattern.lower()
    out: List[str] = []
    for p in walk_files(root, max_files=max_files):
        if fnmatch.fnmatch(os.path.basename(p).lower(), pat):
            out.append(p)
            if len(out) >= max_results:
                break
    return out


@dataclass
class DuplicateGroup:
    size: int
    sha256: str
    paths: List[str]

    @property
    def wasted(self) -> int:
        return self.size * (len(self.paths) - 1)


def find_duplicates(root: str, *, min_size: int = 1, max_files: int = 200_000) -> List[DuplicateGroup]:
    """T0: files with identical content. Groups by size first, then by SHA-256. Reports only, deletes nothing."""
    by_size: Dict[int, List[str]] = defaultdict(list)
    for p in walk_files(root, max_files=max_files):
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if sz >= min_size:
            by_size[sz].append(p)
    groups: List[DuplicateGroup] = []
    for sz, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: Dict[str, List[str]] = defaultdict(list)
        for p in paths:
            try:
                by_hash[sha256_of(p)].append(p)
            except OSError:
                continue
        groups.extend(DuplicateGroup(sz, h, sorted(ps)) for h, ps in by_hash.items() if len(ps) > 1)
    return sorted(groups, key=lambda g: g.wasted, reverse=True)


@dataclass
class BackupResult:
    report: Report
    dest: Optional[str] = None
    entry_id: Optional[str] = None


class FileTools:
    def __init__(self, guard: Guardrails, journal: ChangeJournal, *, clock: Callable[[], float] = time.time):
        self.guard = guard
        self.journal = journal
        self.clock = clock

    # ------------------------------------------------------------------ backup
    def backup(self, src: str, dest_dir: str) -> BackupResult:
        """Copy a file or folder into dest_dir/<name>-backup-<timestamp>, verify every file, journal the undo."""
        task = f"backup {src}"
        src = os.path.abspath(src)
        dest_dir = os.path.abspath(dest_dir)
        if not os.path.exists(src):
            return BackupResult(Report(task, [CheckResult("source", False, "does not exist")]))
        if _is_link(src):
            return BackupResult(Report(task, [CheckResult("source", False, "is a link; refusing to follow it")]))
        if not self.guard.allowlist.is_allowed(dest_dir):
            return BackupResult(Report(task, [CheckResult("destination", False, f"outside allowed folders: {dest_dir}")]))
        base = os.path.basename(src.rstrip("\\/")) or "root"
        if any(part.lower() in PROTECTED_NAMES for part in src.replace("\\", "/").split("/")):
            return BackupResult(Report(task, [CheckResult("source", False, "is a protected credential location")]))
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        tag = f"-backup-{stamp}-{uuid.uuid4().hex[:4]}"
        if os.path.isdir(src):
            dest = os.path.join(dest_dir, base + tag)
        else:  # keep the extension so the backup still opens: report.pdf -> report-backup-....pdf
            stem, ext = os.path.splitext(base)
            dest = os.path.join(dest_dir, stem + tag + ext)
        # a backup must never land inside the thing it is copying (it would copy itself forever)
        if os.path.isdir(src) and os.path.normcase(dest + os.sep).startswith(os.path.normcase(src + os.sep)):
            return BackupResult(Report(task, [CheckResult("destination", False, "is inside the source folder")]))
        if os.path.lexists(dest):
            return BackupResult(Report(task, [CheckResult("destination", False, "already exists; not overwriting")]))
        d = self.guard.authorize_action(f"copy {src} to {dest}", tier=Tier.T1,
                                        reasons=["backup: copy then verify, never overwrites"], intent="backup")
        if not d.allowed:
            return BackupResult(Report(task, [CheckResult("permission", False, "; ".join(d.reasons[-2:]))]))
        os.makedirs(dest_dir, exist_ok=True)
        step = UndoStep("delete_tree" if os.path.isdir(src) else "delete_created", path=dest)
        try:
            self.guard.killswitch.check()
            if os.path.isdir(src):
                shutil.copytree(src, dest, symlinks=True, ignore=shutil.ignore_patterns(*PROTECTED_NAMES))
            else:
                shutil.copy2(src, dest)
        except OSError as e:
            leftover = os.path.lexists(dest)
            eid = self.journal.record(f"backup {base} (partial)", [step], decision_id=d.id, tier="T1") if leftover else None
            return BackupResult(Report(task, [CheckResult("copy", False, f"copy failed: {e}")], [eid] if eid else [],
                                       f"journal.undo(guard, ids=['{eid}'])" if eid else "nothing changed"), dest, eid)
        eid = self.journal.record(f"backup {base}", [step], decision_id=d.id, tier="T1")
        self.guard.record_result(d, ran=True, src=src, dest=dest)
        checks, manifest = self._verify_copy(src, dest)
        if all(c.passed for c in checks):
            self._save_manifest(dest, manifest)  # lets restore_test prove the backup is still intact later
        return BackupResult(Report(task, checks, [eid], f"journal.undo(guard, ids=['{eid}'])"), dest, eid)

    def manifest_path(self, backup_path: str) -> str:
        name = os.path.basename(backup_path.rstrip("\\/"))
        return os.path.join(str(self.journal.snapshot_dir), name + ".manifest.json")

    def _save_manifest(self, backup_path: str, manifest: Dict[str, str]) -> None:
        with open(self.manifest_path(backup_path), "w", encoding="utf-8") as f:
            json.dump(manifest, f, sort_keys=True)

    @staticmethod
    def _verify_copy(src: str, dest: str) -> Tuple[List[CheckResult], Dict[str, str]]:
        """Compare dest to src file by file. Also returns {relative path: sha256 of the SOURCE file}."""
        pairs: List[Tuple[str, str]] = []
        manifest: Dict[str, str] = {}
        if os.path.isdir(src):
            for p in walk_files(src, max_files=10 ** 9):
                pairs.append((p, os.path.join(dest, os.path.relpath(p, src))))
            src_n = len(pairs)
            dest_n = sum(1 for _ in walk_files(dest, max_files=10 ** 9))
            checks = [CheckResult("file count", src_n == dest_n, f"{dest_n} copied of {src_n}")]
        else:
            pairs.append((src, dest))
            checks = []
        bad = []
        for s, d in pairs:
            rel = os.path.relpath(s, src) if os.path.isdir(src) else os.path.basename(s)
            try:
                manifest[rel] = sha256_of(s)
                if not os.path.isfile(d) or os.path.getsize(s) != os.path.getsize(d) or sha256_of(d) != manifest[rel]:
                    bad.append(rel)
            except OSError:
                bad.append(rel)
        checks.append(CheckResult("hash check", not bad,
                                  f"checked {plural(len(pairs), 'file')}, all identical to the source" if not bad
                                  else f"{plural(len(bad), 'file')} changed or missing: {', '.join(bad[:3])}"))
        return checks, manifest

    def restore_test(self, backup_path: str, scratch_dir: str) -> Report:
        """Prove a backup is intact AND restorable: restore it to a fresh scratch folder and check every file against
        the SHA-256 manifest recorded when the backup was made. Comparing a backup only to its own copy would let a
        rotted backup pass, so a backup with no manifest cannot be proven and fails."""
        task = f"restore test {backup_path}"
        if not os.path.exists(backup_path):
            return Report(task, [CheckResult("backup", False, "does not exist")])
        mpath = self.manifest_path(backup_path)
        if not os.path.isfile(mpath):
            return Report(task, [CheckResult("manifest", False, "no manifest was recorded for this backup, so its "
                                                                 "integrity cannot be proven")])
        with open(mpath, encoding="utf-8") as f:
            manifest: Dict[str, str] = json.load(f)
        if not self.guard.allowlist.is_allowed(scratch_dir):
            return Report(task, [CheckResult("scratch", False, f"outside allowed folders: {scratch_dir}")])
        target = os.path.join(os.path.abspath(scratch_dir), "restore-test-" + uuid.uuid4().hex[:6])
        d = self.guard.authorize_action(f"copy {backup_path} to {target}", tier=Tier.T1,
                                        reasons=["restore test into a fresh scratch folder"], intent="restore test")
        if not d.allowed:
            return Report(task, [CheckResult("permission", False, "; ".join(d.reasons[-2:]))])
        os.makedirs(scratch_dir, exist_ok=True)
        step = UndoStep("delete_tree" if os.path.isdir(backup_path) else "delete_created", path=target)
        try:
            if os.path.isdir(backup_path):
                shutil.copytree(backup_path, target, symlinks=True, ignore=shutil.ignore_patterns(*PROTECTED_NAMES))
            else:
                shutil.copy2(backup_path, target)
        except OSError as e:
            eid = (self.journal.record("restore test (partial)", [step], decision_id=d.id, tier="T1")
                   if os.path.lexists(target) else None)
            return Report(task, [CheckResult("restore", False, f"copy failed: {e}")], [eid] if eid else [])
        eid = self.journal.record("restore test", [step], decision_id=d.id, tier="T1")
        bad = []
        for rel, want in manifest.items():
            p = os.path.join(target, rel) if os.path.isdir(target) else target
            try:
                if not os.path.isfile(p) or sha256_of(p) != want:
                    bad.append(rel)
            except OSError:
                bad.append(rel)
        have = sum(1 for _ in walk_files(target, max_files=10 ** 9)) if os.path.isdir(target) else 1
        checks = [CheckResult("file count", have == len(manifest), f"{have} restored of {len(manifest)} recorded"),
                  CheckResult("hash check", not bad,
                              f"checked {plural(len(manifest), 'file')}, all match the backup-time manifest" if not bad
                              else f"{plural(len(bad), 'file')} damaged or missing: {', '.join(bad[:3])}")]
        return Report(task, checks, [eid], f"journal.undo(guard, ids=['{eid}']) removes the scratch copy")
