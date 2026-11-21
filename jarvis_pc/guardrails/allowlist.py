"""Path allowlist: which folders JARVIS may modify.

Handles both Windows-style paths (case-insensitive, drive letters) and POSIX
paths (symlinks resolved), so the same config works on the Windows PC and the
Ubuntu coordinator.
"""
from __future__ import annotations

import ntpath
import os
import re
from typing import Iterable, List, Optional

_WIN_DRIVE = re.compile(r"^[A-Za-z]:([\\/]|$)")
_ENV_VAR = re.compile(r"(\$env:|%[A-Za-z_][A-Za-z0-9_]*%|\$\w|\$\{)", re.I)
_POSIX_TOP = {
    "/tmp", "/etc", "/dev", "/var", "/usr", "/bin", "/sbin", "/opt",
    "/home", "/root", "/mnt", "/sys", "/proc", "/boot", "/lib",
}
_TOKEN = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')


def is_windows_path(p: str) -> bool:
    return bool(_WIN_DRIVE.match(p)) or p.startswith("\\\\")


def normalize(path: str, cwd: Optional[str] = None) -> str:
    """Return a canonical form of ``path`` for comparison."""
    p = path.strip().strip("\"'")
    if is_windows_path(p) or (
        cwd and is_windows_path(cwd) and not p.startswith("/") and not p.startswith("~")
    ):
        full = ntpath.normpath(p if is_windows_path(p) else ntpath.join(cwd, p))  # type: ignore[arg-type]
        if os.name == "nt" and not full.startswith("\\\\"):
            # On the real PC, follow junctions and symlinks in the part that exists: a junction inside an allowed folder
            # that points at C:\Windows must be judged as C:\Windows (Jarvis runs elevated, so the write would succeed).
            try:
                full = os.path.realpath(full)
            except (OSError, ValueError):
                pass
        return ntpath.normcase(full)
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.realpath(p)


def _within(path_n: str, root_n: str) -> bool:
    if is_windows_path(root_n) != is_windows_path(path_n):
        return False
    sep = "\\" if is_windows_path(root_n) else "/"
    prefix = root_n if root_n.endswith(sep) else root_n + sep
    return path_n == root_n.rstrip(sep) or path_n == root_n or path_n.startswith(prefix)


def looks_like_path(token: str) -> bool:
    t = token
    if not t:
        return False
    if _ENV_VAR.search(t) and ("\\" in t or "/" in t):
        return True
    if _WIN_DRIVE.match(t) or t.startswith("\\\\") or t.startswith("~"):
        return True
    if ".." in re.split(r"[\\/]", t):
        return True
    if t.startswith("/") and (t.lower() in _POSIX_TOP or t.count("/") >= 2):
        return True
    return False


def extract_paths(text: str) -> List[str]:
    """Pull path-looking arguments out of a command's argument text."""
    out: List[str] = []
    for m in _TOKEN.finditer(text):
        t = next(g for g in m.groups() if g is not None)
        t = t.rstrip(",;)")
        if t.startswith("-"):
            if "=" in t:
                t = t.split("=", 1)[1]
            else:
                continue
        if looks_like_path(t):
            out.append(t)
    return out


class PathAllowlist:
    def __init__(self, roots: Iterable[str] = ()):
        self.roots = [normalize(r) for r in roots]

    def is_allowed(self, path: str, cwd: Optional[str] = None) -> bool:
        if _ENV_VAR.search(path):
            return False  # unresolved variable: cannot prove it is inside a root
        n = normalize(path, cwd)
        return any(_within(n, r) for r in self.roots)

    def first_violation(self, paths: Iterable[str], cwd: Optional[str] = None) -> Optional[str]:
        for p in paths:
            if not self.is_allowed(p, cwd):
                return p
        return None
