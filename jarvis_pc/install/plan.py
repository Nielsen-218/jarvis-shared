"""Install plans: what will happen, in order, before anything happens."""
from __future__ import annotations

import ntpath
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..guardrails.gate import Guardrails
from ..guardrails.tiers import classify
from ..security.repo_trust import TrustReport
from ..security.script_review import ReadmeReview
from ..verify.journal import UndoStep

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{1,100}$")
_UNSAFE_IN_PATH = re.compile(r"['\"`$;\r\n|&<>(){}]")
_SILENT_ARGS = re.compile(r"^[A-Za-z0-9 /\-_=.]{1,80}$")


def quote(s: str) -> str:
    """Single-quote a value for PowerShell/bash. Refuses characters that could break out."""
    if _UNSAFE_IN_PATH.search(s):
        raise ValueError(f"unsafe characters in value: {s!r}")
    return f"'{s}'"


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


@dataclass
class Step:
    name: str
    kind: str  # command | download | scan | note
    command: Optional[str] = None
    cwd: Optional[str] = None
    timeout: float = 600.0
    undo: List[UndoStep] = field(default_factory=list)
    url: Optional[str] = None
    dest: Optional[str] = None
    sha256: Optional[str] = None
    max_bytes: int = 500 * 1024 * 1024
    path: Optional[str] = None
    note: str = ""


@dataclass
class VerifySpec:
    kind: str  # command | launch | file | dir
    command: Optional[str] = None
    expect: Optional[str] = None
    path: Optional[str] = None
    expect_alive: bool = True
    cwd: Optional[str] = None


@dataclass
class InstallPlan:
    title: str
    method: str  # winget | release-zip | release-msi | release-exe | source-build
    steps: List[Step] = field(default_factory=list)
    verify: List[VerifySpec] = field(default_factory=list)
    trust: Optional[TrustReport] = None
    readme_review: Optional[ReadmeReview] = None
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None

    def render(self, guard: Optional[Guardrails] = None) -> str:
        out = [f"PLAN: {self.title}", f"Method: {self.method}"]
        if self.blocked_reason:
            out.append(f"BLOCKED: {self.blocked_reason}")
        if self.trust:
            out.append(f"Repo trust: {self.trust.score}/100 -> {self.trust.verdict}")
            out.extend(f"  {r}" for r in self.trust.reasons)
            out.extend(f"  RED FLAG: {f}" for f in self.trust.red_flags)
        if self.readme_review:
            if self.readme_review.injection.suspicious:
                out.append("README contains text that tries to instruct an AI (treated as data, not followed):")
                out.extend(f"  [{f.rule}] {f.excerpt}" for f in self.readme_review.injection.findings)
            highs = [f for f in self.readme_review.findings if f.severity != "INFO"]
            if highs:
                out.append("README commands worth a look (NOT executed by JARVIS):")
                out.extend(f"  [{f.severity}] {f.rule}: {f.excerpt}" for f in highs[:15])
        out.append("Steps:")
        for i, s in enumerate(self.steps, 1):
            label = s.command or (f"download {s.url}" if s.kind == "download" else s.note or s.kind)
            tier = ""
            if guard and s.kind == "command" and s.command:
                tier = f"[{classify(s.command, s.cwd, guard.allowlist).tier.name}] "
            elif s.kind == "download":
                tier = "[T2] "
            out.append(f"  {i}. {s.name}: {tier}{label}")
        if self.verify:
            out.append("Verification:")
            for v in self.verify:
                out.append(f"  - {v.kind}: {v.command or v.path}")
        out.extend(f"Warning: {w}" for w in self.warnings)
        out.extend(f"Note: {n}" for n in self.notes)
        return "\n".join(out)


# ---- builders ----------------------------------------------------------------------
def path_module(platform: str):
    return ntpath if platform == "windows" else posixpath


def winget_plan(package_id: str, verify: Sequence[VerifySpec] = ()) -> InstallPlan:
    if not SAFE_ID.match(package_id):
        raise ValueError(f"bad winget id: {package_id!r}")
    plan = InstallPlan(f"{package_id} (winget)", "winget")
    plan.steps.append(Step(
        "install", "command",
        command=f"winget install --id {package_id} --exact --silent "
                "--accept-package-agreements --accept-source-agreements",
        undo=[UndoStep("command", command=f"winget uninstall --id {package_id} --exact --silent")],
    ))
    plan.verify.append(VerifySpec("command",
                                  command=f"winget list --id {package_id} --exact --accept-source-agreements",
                                  expect=package_id))
    plan.verify.extend(verify)
    return plan


_SKIP_ASSET = re.compile(r"(\.sig|\.asc|\.sha256|\.sha512|\.txt|\.sbom|\.pdb|\.json|\.pem|\.minisig|\.deb|\.rpm|\.dmg|\.appimage|\.pkg|\.tar|\.gz|\.xz|\.7z|\.apk)$", re.I)
_OTHER_OS = re.compile(r"(linux|darwin|macos|osx|android|freebsd|ubuntu|debian)", re.I)
_EXT_RANK = {".zip": 0, ".msi": 1, ".exe": 2}


def choose_asset(release: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pick the Windows installer/zip from a release's assets, or None."""
    best, best_key = None, None
    for a in release.get("assets", []):
        name = a.get("name", "")
        ext = ntpath.splitext(name)[1].lower()
        if ext not in _EXT_RANK or _SKIP_ASSET.search(name) or _OTHER_OS.search(name):
            continue
        low = name.lower()
        win = 0 if re.search(r"win|windows", low) else 1
        arch = 0 if re.search(r"x64|amd64|x86_64|64", low) else 1
        arm = 1 if re.search(r"arm|aarch", low) else 0
        gnu = 1 if "gnu" in low else 0  # prefer the standard MSVC build over MinGW when both exist
        key = (arm, win, arch, _EXT_RANK[ext], gnu)
        if best_key is None or key < best_key:
            best, best_key = a, key
    return best


def find_checksum_asset(release: Dict[str, Any], asset_name: str) -> Optional[Dict[str, Any]]:
    for a in release.get("assets", []):
        n = a.get("name", "").lower()
        if n == asset_name.lower() + ".sha256" or re.search(r"(sha256sums?|checksums?)(\.txt)?$", n):
            return a
    return None


def detect_build_system(root_files: Sequence[str]) -> Optional[str]:
    names = {f.lower() for f in root_files}
    if names & {"pyproject.toml", "setup.py", "requirements.txt"}:
        return "python"
    if "package.json" in names:
        return "node"
    if "cargo.toml" in names:
        return "rust"
    if "go.mod" in names:
        return "go"
    if "cmakelists.txt" in names:
        return "cmake"
    if any(n.endswith((".sln", ".csproj")) for n in names):
        return "dotnet"
    return None


def source_build_commands(system: str, root_files: Sequence[str], pm=ntpath) -> List[str]:
    names = {f.lower() for f in root_files}
    if system == "python":
        py = pm.join(".venv", "Scripts", "python.exe") if pm is ntpath else pm.join(".venv", "bin", "python")
        install = f"{py} -m pip install -r requirements.txt" if "requirements.txt" in names \
            else f"{py} -m pip install ."
        return ["python -m venv .venv", install]
    if system == "node":
        return ["npm ci --ignore-scripts" if "package-lock.json" in names else "npm install --ignore-scripts",
                "npm run build"]
    if system == "rust":
        return ["cargo build --release"]
    if system == "go":
        return ["go build ./..."]
    if system == "cmake":
        return ["cmake -S . -B build", "cmake --build build --config Release"]
    if system == "dotnet":
        return ["dotnet build -c Release"]
    return []
