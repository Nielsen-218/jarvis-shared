"""Minimal GitHub reader for the install pipeline (read-only API calls)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_GITHUB_HOSTS = {"github.com", "www.github.com"}
DOWNLOAD_HOST_SUFFIXES = ("github.com", "githubusercontent.com")


class GitHubError(Exception):
    pass


def parse_github_url(spec: str) -> Tuple[str, str]:
    """Accept https://github.com/owner/repo[.git][/...] or owner/repo. Rejects lookalike hosts."""
    s = spec.strip()
    if "://" in s:
        u = urllib.parse.urlparse(s)
        if u.scheme != "https" or (u.hostname or "").lower() not in _GITHUB_HOSTS or u.username or u.port:
            raise GitHubError(f"not a plain https://github.com link: {spec!r}")
        parts = [p for p in u.path.split("/") if p]
    else:
        parts = [p for p in s.split("/") if p]
        if len(parts) != 2:
            raise GitHubError(f"expected owner/repo or a GitHub URL: {spec!r}")
    if len(parts) < 2:
        raise GitHubError(f"no repository in link: {spec!r}")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _NAME.match(owner) or not _NAME.match(repo) or ".." in owner + repo:
        raise GitHubError(f"unsafe owner/repo name: {owner!r}/{repo!r}")
    return owner, repo


def host_allowed(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    h = (u.hostname or "").lower()
    return u.scheme == "https" and any(h == s or h.endswith("." + s) for s in DOWNLOAD_HOST_SUFFIXES)


def decode_text(raw: bytes) -> str:
    """Decode a downloaded text file. Windows tools sometimes write UTF-16."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def default_fetch(url: str, headers: Dict[str, str], timeout: float = 20.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, decode_text(r.read(2_000_000))
    except urllib.error.HTTPError as e:
        return e.code, decode_text(e.read(200_000))


Fetch = Callable[[str, Dict[str, str]], Tuple[int, str]]


class GitHubClient:
    def __init__(self, token: Optional[str] = None, fetch: Optional[Fetch] = None):
        self._fetch = fetch or default_fetch
        self._headers = {"User-Agent": "jarvis-pc-installer", "Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _get(self, url: str, accept: Optional[str] = None, allow_404: bool = False) -> Optional[str]:
        h = dict(self._headers)
        if accept:
            h["Accept"] = accept
        status, body = self._fetch(url, h)
        if status == 404 and allow_404:
            return None
        if status != 200:
            raise GitHubError(f"GitHub returned {status} for {url}")
        return body

    def repo(self, owner: str, name: str) -> Dict[str, Any]:
        return json.loads(self._get(f"https://api.github.com/repos/{owner}/{name}") or "{}")

    def releases(self, owner: str, name: str) -> List[Dict[str, Any]]:
        body = self._get(f"https://api.github.com/repos/{owner}/{name}/releases?per_page=10", allow_404=True)
        return json.loads(body) if body else []

    def readme(self, owner: str, name: str) -> str:
        return self._get(f"https://api.github.com/repos/{owner}/{name}/readme",
                         accept="application/vnd.github.raw", allow_404=True) or ""

    def root_listing(self, owner: str, name: str) -> List[str]:
        body = self._get(f"https://api.github.com/repos/{owner}/{name}/contents/", allow_404=True)
        return [e["name"] for e in json.loads(body)] if body else []

    def text(self, url: str) -> str:
        """Fetch a small text asset (e.g. a checksum file) from an allowed download host."""
        if not host_allowed(url):
            raise GitHubError(f"refusing to fetch from {url}")
        return self._get(url, accept="application/octet-stream") or ""
