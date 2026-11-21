"""Hash and code-signature checks for downloaded files."""
from __future__ import annotations

import hashlib
import ntpath
import os
import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..verify.checks import CheckResult


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_HEX64 = r"[A-Fa-f0-9]{64}"
_CERTUTIL = re.compile(
    r"^\s*SHA256 hash (?:of(?: file)? )?(?P<name>.+?):?\s*\n\s*(?P<hash>[A-Fa-f0-9 ]{64,100}?)\s*\n", re.I | re.M)


def _base(name: str) -> str:
    return ntpath.basename(posixpath.basename(name.strip().strip("\"'")))


def parse_checksums(text: str, filename: str) -> Optional[str]:
    """Find the SHA-256 for `filename` in a checksum file. Returns None if it can't be sure.

    Understands: `hash  filename` lines (sha256sum / shasum), certutil's
    `SHA256 hash of <file>:` + hash line, and a file holding exactly one hash.
    If the file names a different file for its hash, or holds several hashes
    without saying which is ours, the answer is None (never a guess).
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    base = _base(filename)

    named_elsewhere = False
    for line in text.splitlines():
        m = re.match(rf"^\s*({_HEX64})\s+\*?(.+?)\s*$", line)
        if m:
            if _base(m.group(2)) == base:
                return m.group(1).lower()
            named_elsewhere = True

    for m in _CERTUTIL.finditer(text):
        digest = m.group("hash").replace(" ", "")
        if re.fullmatch(_HEX64, digest):
            if _base(m.group("name")) == base:
                return digest.lower()
            named_elsewhere = True

    if named_elsewhere:
        return None
    unique = {t.lower() for t in re.findall(rf"(?<![A-Fa-f0-9])({_HEX64})(?![A-Fa-f0-9])", text)}
    if len(unique) == 1:
        return unique.pop()
    return None


def compare_hash(path: str, expected: Optional[str]) -> CheckResult:
    name = f"sha256 {path}"
    actual = sha256_file(path)
    if not expected:
        return CheckResult(name, True, "no published checksum to compare (hash recorded only)", actual)
    if actual.lower() != expected.lower():
        return CheckResult(name, False, "HASH MISMATCH: file differs from the published checksum", actual)
    return CheckResult(name, True, "matches published checksum", actual)


@dataclass
class SignatureInfo:
    status: str  # Valid | NotSigned | HashMismatch | NotTrusted | UnknownError | Unavailable
    message: str = ""


def parse_signature_status(text: str) -> Optional[str]:
    m = re.search(r"^Status\s*:\s*(\w+)", text, re.M)
    return m.group(1) if m else None


def _quote(path: str) -> str:
    if re.search(r"['\"`$;\r\n|&<>(){}]", path):
        raise ValueError(f"unsafe characters in path: {path!r}")
    return f"'{path}'"


def signature_check(runner: Callable, path: str) -> SignatureInfo:
    """Windows Authenticode check, run through the gate (read-only, T0)."""
    res = runner(f"Get-AuthenticodeSignature -FilePath {_quote(path)} | Format-List Status,StatusMessage")
    if not res.ran or not res.ok:
        return SignatureInfo("Unavailable", (res.stderr or "could not run")[:200])
    status = parse_signature_status(res.stdout)
    return SignatureInfo(status or "UnknownError", res.stdout.strip()[:300])


SIGNABLE_EXT = (".exe", ".msi", ".dll", ".sys", ".ps1")  # checked in this order; .exe/.msi matter most


@dataclass
class DirSignatureInfo:
    checked: int  # files actually checked
    total: int  # signable files found in the folder
    counts: Dict[str, int] = field(default_factory=dict)  # status -> how many files
    tampered: List[str] = field(default_factory=list)  # files whose signature is present but broken (HashMismatch)
    message: str = ""


def signature_check_dir(runner: Callable, folder: str, max_files: int = 25) -> DirSignatureInfo:
    """Authenticode-check the executables inside an extracted folder (read-only, T0 per file).

    Get-AuthenticodeSignature cannot take a folder, so calling signature_check on one always answered "unavailable" and
    an extracted download was never really checked. This lists the signable files in Python (never following links) and
    checks each through the gate, up to max_files, executables first. A file with a broken signature is reported by name.
    """
    found: List[str] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        found.extend(os.path.join(root, f) for f in files
                     if f.lower().endswith(SIGNABLE_EXT) and not os.path.islink(os.path.join(root, f)))
    found.sort(key=lambda p: (SIGNABLE_EXT.index(os.path.splitext(p)[1].lower()), p.lower()))
    info = DirSignatureInfo(0, len(found))
    if not found:
        info.message = "no executable files to check"
        return info
    for path in found[:max_files]:
        try:
            status = signature_check(runner, path).status
        except ValueError:  # a file name the gate would not let us pass to PowerShell safely
            status = "Skipped"
        info.checked += 1
        info.counts[status] = info.counts.get(status, 0) + 1
        if status == "HashMismatch":
            info.tampered.append(path)
    shown = ", ".join(f"{n} {k}" for k, n in sorted(info.counts.items()))
    info.message = f"checked {info.checked} of {info.total}: {shown}"
    return info

