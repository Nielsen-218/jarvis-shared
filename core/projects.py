"""Find a project or launchable file by the words the owner uses for it, with no project-specific knowledge.

"find my shadow world project" -> the words shadow, world -> every .uproject / .sln / .blend / .code-workspace / launcher under the
usual roots whose folder or file name contains them (joined or apart: LivingShadowWorld), ranked so the project's own folder beats
a worktree, a package output or a copy; two candidates that tie are reported as a choice, never guessed. Read-only.
"""
from __future__ import annotations

import os
import re
import string
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

KINDS = {".uproject": "Unreal project", ".sln": "Visual Studio solution", ".blend": "Blender file", ".code-workspace": "VS Code workspace",
         ".godot": "Godot project", ".unity": "Unity scene", ".exe": "program"}
LAUNCHABLE = set(KINDS) | {".lnk", ".bat", ".cmd", ".txt", ".md", ".pdf", ".py", ".json", ".html"}
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "intermediate", "saved", "binaries", "deriveddatacache",
             "build", "dist", "obj", "bin", "library", "temp", "tmp", "cache", ".cache", "appdata", "packages", "plugins", "content", "source",
             "third_party", "thirdparty", "external", "vendor", "logs", ".worktrees", "backups", "quarantine", "snapshots", "stagedbuilds",
             "windows", "windowsnoeditor", "linux", "android"}
# Words that say nothing about WHICH project is meant.
_NOISE = {"my", "the", "a", "an", "project", "projects", "game", "unreal", "ue", "ue5", "ue4", "engine", "file", "folder", "app", "program",
          "solution", "blender", "scene", "launch", "open", "find", "start", "run", "it", "and", "then", "please", "jarvis", "for", "me", "up"}
MAX_DEPTH = 5
MAX_ENTRIES = 60000


@dataclass
class Candidate:
    path: str
    kind: str
    score: float
    modified: float
    why: List[str] = field(default_factory=list)

    def brief(self) -> str:
        return f"{self.path} ({self.kind})"


def words_of(name: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]{2,}", (name or "").casefold()) if w not in _NOISE]


def roots() -> List[str]:
    """Where projects live: the profile's top-level folders, Documents/Desktop, and every fixed drive's top level."""
    out: List[str] = []
    home = os.path.expanduser("~")
    for sub in ("", "Documents", "Desktop", "source", "repos", "Projects", "UnrealProjects", "Unreal Projects", "dev", "code", "src", "Games"):
        p = os.path.join(home, sub) if sub else home
        if os.path.isdir(p) and p not in out:
            out.append(p)
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if letter != "C" and os.path.isdir(drive) and drive not in out:
                out.append(drive)
        for p in (r"C:\Projects", r"C:\Unreal Projects", r"C:\UnrealProjects", r"C:\dev", r"C:\src", r"C:\Games"):
            if os.path.isdir(p) and p not in out:
                out.append(p)
    return out


def _walk(root: str, max_depth: int, budget: list) -> Iterable[os.DirEntry]:
    """Breadth first: the shallow folders where projects usually live are seen before the budget can run out in one deep
    tree (2026-11-06: a throwaway project two folders under Documents was never reached after the profile's big trees)."""
    from collections import deque
    stack = deque([(root, 0)])
    while stack:
        folder, depth = stack.popleft()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        for e in entries:
            budget[0] -= 1
            if budget[0] <= 0:
                return
            try:
                if e.is_dir(follow_symlinks=False):
                    name = e.name.casefold()
                    if depth < max_depth and name not in SKIP_DIRS and not name.startswith((".", "$", "~")):
                        stack.append((e.path, depth + 1))
                elif e.is_file(follow_symlinks=False):
                    yield e
            except OSError:
                continue


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def find(name: str, search_roots: Optional[List[str]] = None, *, kinds: Optional[Iterable[str]] = None, limit: int = 3,
         max_depth: int = MAX_DEPTH) -> List[Candidate]:
    """Launchable files matching the words of `name`, best first. A candidate must contain EVERY distinctive word (in its
    file name or its folder names, joined or apart). Score: the file's kind (a .uproject beats a stray .exe), the words in the
    FILE name or its OWN folder (the real project folder beats LSW_wt or a package output that merely contains a copy),
    shallower paths, then recency."""
    want = words_of(name)
    if not want:
        return []
    exts = set(kinds) if kinds else set(KINDS)
    out: List[Candidate] = []
    near: List[tuple] = []                          # right kind, words not all there: for the close-spelling pass
    seen, seen_near = set(), set()
    for root in (search_roots if search_roots is not None else roots()):
        budget = [MAX_ENTRIES]                      # each place gets its own budget: one huge tree cannot starve the others
        for e in _walk(root, max_depth, budget):
            ext = os.path.splitext(e.name)[1].casefold()
            if ext not in exts:
                continue
            path = e.path
            low = path.casefold()
            if low in seen:
                continue
            squashed = _squash(path)
            if not all(w in squashed for w in want):
                if ext != ".exe" and len(near) < 5000 and low not in seen_near:
                    seen_near.add(low)                  # the profile and Documents are both searched: one file, once
                    near.append((e, ext))
                continue
            seen.add(low)
            why: List[str] = []
            kind_rank = {".uproject": 5, ".sln": 4, ".blend": 3, ".code-workspace": 3, ".godot": 3, ".unity": 2, ".exe": 1}.get(ext, 1)
            score = kind_rank * 10.0
            stem = _squash(os.path.splitext(e.name)[0])
            parent = _squash(os.path.basename(os.path.dirname(path)))
            if all(w in stem for w in want):
                score += 20; why.append("the file is named after it")
            if all(w in parent for w in want):
                score += 15; why.append("it sits in its own folder")
            depth = low.count(os.sep)
            score -= depth
            if ext == ".exe" and re.search(r"(?:setup|install|unins|updat|crash|helper|launcher-?update)[^\\/]*\.exe$", low):
                score -= 30; why.append("looks like an installer or helper")
            try:
                modified = e.stat().st_mtime
            except OSError:
                modified = 0.0
            out.append(Candidate(path, KINDS.get(ext, ext), round(score, 1), modified, why))
    if not out and near:
        out = _close_spellings(want, near)
    out.sort(key=lambda c: (-c.score, -c.modified, len(c.path)))
    return out[:limit]


CLOSE = 0.85


def _close_spellings(want: List[str], near: List[tuple]) -> List[Candidate]:
    """Speech recognition writes 'Quilmere' for 'Quillmere'. When no project has every word, one whose file or folder name
    is a close spelling of the words (similarity >= 0.85, letters and digits only) still counts, and says so."""
    from difflib import SequenceMatcher
    target = "".join(want)
    if len(target) < 6:
        return []                                   # short names are too easy to confuse
    found: List[Candidate] = []
    for e, ext in near:
        stem = _squash(os.path.splitext(e.name)[0])
        parent = _squash(os.path.basename(os.path.dirname(e.path)))
        best = max(SequenceMatcher(None, target, stem).ratio(), SequenceMatcher(None, target, parent).ratio())
        if best >= CLOSE:
            try:
                modified = e.stat().st_mtime
            except OSError:
                modified = 0.0
            rank = {".uproject": 5, ".sln": 4, ".blend": 3, ".code-workspace": 3, ".godot": 3, ".unity": 2}.get(ext, 1)
            found.append(Candidate(e.path, KINDS.get(ext, ext), round(rank * 10 + best * 10 - e.path.count(os.sep), 1), modified,
                                   [f"a close spelling of '{' '.join(want)}'"]))
    return found


def ambiguous(found: List[Candidate]) -> bool:
    """Two candidates close enough that picking one would be a guess (same kind, within 2 points)."""
    return len(found) >= 2 and found[0].kind == found[1].kind and found[0].score - found[1].score < 2.0


def launch_method(path: str) -> str:
    ext = os.path.splitext(path)[1].casefold()
    if ext == ".uproject":
        return "open it with the Unreal Editor registered for .uproject files"
    if ext == ".sln":
        return "open it in Visual Studio"
    if ext == ".blend":
        return "open it in Blender"
    if ext == ".code-workspace":
        return "open it in VS Code"
    if ext in (".exe", ".bat", ".cmd", ".lnk"):
        return "run it"
    if os.path.isdir(path):
        return "open the folder in File Explorer"
    return "open it with its default program"


def expected_title_words(path: str) -> List[str]:
    """Words a window title should carry once the path opened: the file's stem (and the project folder for a .uproject)."""
    stem = os.path.splitext(os.path.basename(path.rstrip("\\/")))[0]
    out = [w for w in re.split(r"[\s_\-.]+", stem) if len(w) >= 3]
    return out or [stem]


def describe(found: List[Candidate]) -> str:
    if not found:
        return "I did not find a project by that name in the usual places."
    lines = [f"{i + 1}) {c.path} ({c.kind}, {time.strftime('%Y-%m-%d', time.localtime(c.modified)) if c.modified else 'date unknown'}"
             + (", " + ", ".join(c.why) if c.why else "") + ")" for i, c in enumerate(found)]
    return "Found: " + "; ".join(lines) + "."
