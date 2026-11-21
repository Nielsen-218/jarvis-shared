"""Static review of scripts and README code blocks before anything is run.

Pattern based, so it misses clever obfuscation. A clean review is not a
green light. A flagged one is a reason to stop and show you the line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

from ..guardrails.injection import ScanResult, scan_untrusted
from ..guardrails.tiers import PROTECTED_PATTERNS, T3_PATTERNS

_F = re.I


@dataclass
class ScriptFinding:
    severity: str  # HIGH | MEDIUM | INFO
    rule: str
    line_no: int
    excerpt: str


_EXTRA: List[Tuple[str, str, str]] = [
    ("HIGH", "persistence-run-key", r"currentversion[\\/]+run(once)?\b"),
    ("HIGH", "persistence-startup-folder", r"start menu[\\/]+programs[\\/]+startup|shell:startup"),
    ("HIGH", "lolbin-download", r"\bcertutil\b.*-urlcache|\bbitsadmin\b.*/transfer|\bmshta\b"),
    ("HIGH", "obfuscated-blob", r"[A-Za-z0-9+/=]{200,}"),
    ("MEDIUM", "scheduled-task", r"\bschtasks\b.*/create|register-scheduledtask|new-scheduledtask"),
    ("MEDIUM", "creates-service", r"\bnew-service\b|\bsc(\.exe)?\s+create\b"),
    ("MEDIUM", "execution-policy", r"set-executionpolicy\s+(bypass|unrestricted)|-executionpolicy\s+bypass"),
    ("MEDIUM", "hidden-window", r"-windowstyle\s+hidden|-w\s+hidden"),
    ("MEDIUM", "downloads-file", r"\b(invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer)\b"),
    ("MEDIUM", "registry-write", r"\breg\s+add\b|set-itemproperty\s+.*hk(lm|cu)"),
    ("MEDIUM", "needs-admin", r"\bsudo\b|runas|-verb\s+runas|requireadministrator"),
    ("INFO", "chmod-exec", r"chmod\s+\+x"),
]


def review_script(text: str) -> List[ScriptFinding]:
    findings: List[ScriptFinding] = []
    seen = set()
    for no, line in enumerate(text.splitlines(), 1):
        for pat, why in T3_PATTERNS:
            if re.search(pat, line, _F | re.S) and (why, no) not in seen:
                seen.add((why, no))
                findings.append(ScriptFinding("HIGH", why, no, line.strip()[:160]))
        for pat, why in PROTECTED_PATTERNS:
            if re.search(pat, line, _F) and (why, no) not in seen:
                seen.add((why, no))
                findings.append(ScriptFinding("HIGH", f"touches {why}", no, line.strip()[:160]))
        for sev, rule, pat in _EXTRA:
            if re.search(pat, line, _F) and (rule, no) not in seen:
                seen.add((rule, no))
                findings.append(ScriptFinding(sev, rule, no, line.strip()[:160]))
    return findings


_FENCE = re.compile(r"```[ \t]*([\w+-]*)[^\n]*\n(.*?)```", re.S)


def extract_code_blocks(markdown: str) -> List[Tuple[str, str]]:
    return [(m.group(1).lower(), m.group(2)) for m in _FENCE.finditer(markdown)]


@dataclass
class ReadmeReview:
    injection: ScanResult
    findings: List[ScriptFinding] = field(default_factory=list)

    @property
    def high_risk(self) -> bool:
        return self.injection.suspicious or any(f.severity == "HIGH" for f in self.findings)


def review_readme(text: str) -> ReadmeReview:
    findings: List[ScriptFinding] = []
    for _lang, code in extract_code_blocks(text):
        findings.extend(review_script(code))
    return ReadmeReview(scan_untrusted(text), findings)
