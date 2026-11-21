"""School subjects for reading a git repository (blueprint area 12), graded against a real throw-away repository the harness builds.

The harness creates the repository with git itself (init, commit, edit), so the right answer is known from how it was built, not
from the commands being taught. The teacher only ever gets READ-ONLY git: the gate lets `git -C <folder> status|log|branch|
rev-list|rev-parse|show|diff` run automatically and still asks for anything that changes work, writes a file, or runs a program.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Tuple

from .curriculum import Absent, Context, HIGH_SCHOOL, Num, Subject, Task


def available() -> bool:
    return bool(shutil.which("git"))


def _git(repo: str, *args: str, author: str = "School Bot") -> None:
    subprocess.run(["git", "-C", repo, "-c", f"user.name={author}", "-c", "user.email=school@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", "core.autocrlf=false", *args], capture_output=True, text=True, timeout=60, check=True)


def _repo(c: Context, commits: int = 3, branch: str = "", author: str = "School Bot") -> Tuple[str, List[str], List[str]]:
    """A repository with `commits` commits; returns (path, commit subjects oldest first, file names)."""
    repo = c.folder(c.word(6))
    _git(repo, "init", "-q", "-b", branch or "main", author=author)
    files, subjects = [], []
    for i in range(commits):
        name = c.word(5) + ".txt"
        with open(os.path.join(repo, name), "w", encoding="utf-8", newline="") as f:
            f.write(c.word(12) + chr(10))
        files.append(name)
        _git(repo, "add", "-A", author=author)
        msg = c.word(9)
        _git(repo, "commit", "-q", "-m", msg, author=author)
        subjects.append(msg)
    return repo, subjects, files


def _none(ask: str) -> Task:
    return Task(ask, {"repo": "x"}, [], None, 0)


def _branch(c: Context) -> Task:
    ask = "what branch is the git repository at {repo} on"
    if not available():
        return _none(ask)
    branch = "feature-" + c.word(5)
    repo, _s, _f = _repo(c, 2, branch)
    return Task(ask, {"repo": repo}, [branch, Absent(("master",))], None, 0)


def _commit_count(c: Context) -> Task:
    ask = "how many commits are in the git repository at {repo}"
    if not available():
        return _none(ask)
    n = c.rng.randint(3, 8)
    repo, _s, _f = _repo(c, n)
    return Task(ask, {"repo": repo}, [Num(n, 0.0)], None, 0)


def _last_message(c: Context) -> Task:
    ask = "what is the message of the latest commit in the git repository at {repo}"
    if not available():
        return _none(ask)
    repo, subjects, _f = _repo(c, c.rng.randint(3, 6))
    return Task(ask, {"repo": repo}, [subjects[-1], Absent(tuple(subjects[:-1]))], None, 0)


def _author(c: Context) -> Task:
    ask = "who wrote the latest commit in the git repository at {repo}"
    if not available():
        return _none(ask)
    author = c.word(5).title() + " " + c.word(6).title()
    repo, _s, _f = _repo(c, 2, author=author)
    return Task(ask, {"repo": repo}, [author, Absent(("School Bot",))], None, 0)


def _changed_files(c: Context) -> Task:
    ask = "which files have uncommitted changes in the git repository at {repo}"
    if not available():
        return _none(ask)
    repo, _s, files = _repo(c, 4)
    changed = files[:2]
    for name in changed:
        with open(os.path.join(repo, name), "a", encoding="utf-8", newline="") as f:
            f.write("changed " + c.word(6) + chr(10))
    return Task(ask, {"repo": repo}, [*changed, Absent(tuple(files[2:]))], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    mk = lambda i, t, f, al, **k: Subject(i, t, hs, f, al, **k)  # noqa: E731
    return [
        mk("git_branch", "which branch a repository is on", _branch, ("current branch", "git branch name")),
        mk("git_commit_count", "how many commits", _commit_count, ("commit count", "git history length"), needs=("git_branch",)),
        mk("git_last_message", "latest commit message", _last_message, ("last commit", "git log message"), needs=("git_branch",)),
        mk("git_author", "who wrote the latest commit", _author, ("commit author", "git blame author"), needs=("git_last_message",)),
        mk("git_changed_files", "uncommitted changes", _changed_files, ("git status", "modified files"), needs=("git_branch",)),
    ]
