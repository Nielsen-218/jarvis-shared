"""Microsoft Defender scanning via PowerShell, run through the gate.

Fail closed: if the scan cannot run, the result is a FAIL, never a pass.
We never disable Defender or add exclusions (those are T3 in the gate).
"""
from __future__ import annotations

import os
import re
from typing import Callable, List, Optional, Set

from ..verify.checks import CheckResult
from .hash_check import _quote, signature_check, signature_check_dir

_THREAT_ID = re.compile(r"^ThreatID\s*:\s*(\S+)", re.M)
_LIST_THREATS = "Get-MpThreatDetection | Format-List ThreatID,InitialDetectionTime,Resources"


def parse_threat_ids(text: str) -> Set[str]:
    return set(_THREAT_ID.findall(text))


def _threats(runner: Callable) -> Optional[Set[str]]:
    r = runner(_LIST_THREATS)
    if not r.ran or not r.ok:
        return None
    return parse_threat_ids(r.stdout)


def scan_file(runner: Callable, path: str, timeout: float = 900.0) -> CheckResult:
    name = f"Defender scan {path}"
    try:
        target = _quote(path)
    except ValueError as e:
        return CheckResult(name, False, str(e))
    runner("Update-MpSignature", 300.0)  # best effort; a failure here is not fatal
    before = _threats(runner)
    if before is None:
        return CheckResult(name, False, "cannot read Defender detections (is Defender available and enabled?)")
    r = runner(f"Start-MpScan -ScanType CustomScan -ScanPath {target}", timeout)
    if not r.ran or not r.ok:
        return CheckResult(name, False, f"scan did not complete: {(r.stderr or r.error_kind or 'blocked')[:200]}")
    after = _threats(runner)
    if after is None:
        return CheckResult(name, False, "scan ran but results could not be read")
    new = after - before
    if new:
        return CheckResult(name, False, f"THREAT DETECTED (ids: {', '.join(sorted(new))})")
    return CheckResult(name, True, "no new threats detected")


def windows_scanner(runner: Callable) -> Callable[[str], List[CheckResult]]:
    """Default scanner for the install pipeline: Defender scan plus signature check."""
    def scan(path: str) -> List[CheckResult]:
        results = [scan_file(runner, path)]
        name = f"signature {path}"
        if os.path.isdir(path):  # an extracted folder: check the files inside it, not the folder itself
            d = signature_check_dir(runner, path)
            if d.tampered:
                results.append(CheckResult(name, False, "signature present but TAMPERED with: " +
                                           ", ".join(os.path.basename(t) for t in d.tampered[:3])))
            else:
                results.append(CheckResult(name, True, d.message + ("; many open-source tools are unsigned"
                                                                    if d.counts.get("Valid", 0) < d.checked else "")))
            return results
        sig = signature_check(runner, path)
        if sig.status == "Valid":
            results.append(CheckResult(name, True, "validly signed"))
        elif sig.status == "HashMismatch":
            results.append(CheckResult(name, False, "signature present but file was TAMPERED with"))
        elif sig.status in ("NotSigned", "NotTrusted", "UnknownError"):
            results.append(CheckResult(name, True, f"not verifiably signed ({sig.status}); many open-source tools are unsigned"))
        else:
            results.append(CheckResult(name, True, f"signature check unavailable ({sig.message[:80]})"))
        return results
    return scan
