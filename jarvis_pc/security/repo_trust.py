"""Judge how trustworthy a GitHub repo looks before installing from it.

This is a screening aid, not proof. A high score does not make code safe, it
only means the usual warning signs are absent. The install pipeline still
asks you, scans the download, and verifies the result.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

_TS = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class TrustReport:
    score: int
    verdict: str  # PROCEED | ASK | STOP
    reasons: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def assess_repo(meta: Dict[str, Any], *, has_releases: bool = False, has_readme: bool = True,
                known_names: Iterable[str] = (), now: Optional[datetime] = None) -> TrustReport:
    now = now or datetime.now(timezone.utc)
    score = 50
    reasons: List[str] = []
    flags: List[str] = []

    def add(delta: int, why: str) -> None:
        nonlocal score
        score += delta
        reasons.append(f"{'+' if delta >= 0 else ''}{delta} {why}")

    created = _parse(meta.get("created_at"))
    pushed = _parse(meta.get("pushed_at"))
    stars = int(meta.get("stargazers_count") or 0)
    forks = int(meta.get("forks_count") or 0)

    age_days = (now - created).days if created else None
    if age_days is None:
        add(-10, "creation date unknown")
    elif age_days < 30:
        add(-25, f"very new repo ({age_days} days old)")
    elif age_days < 180:
        add(-10, f"young repo ({age_days} days old)")
    elif age_days >= 730:
        add(10, f"established repo ({age_days // 365}+ years old)")

    if stars < 10:
        add(-15, f"almost no stars ({stars})")
    elif stars >= 10000:
        add(20, f"widely used ({stars} stars)")
    elif stars >= 1000:
        add(15, f"popular ({stars} stars)")
    elif stars >= 100:
        add(5, f"some adoption ({stars} stars)")
    if forks >= 50:
        add(3, f"{forks} forks")

    if age_days is not None and age_days < 60 and stars < 50:
        flags.append("new repo with little adoption")
    if meta.get("fork"):
        add(-15, "this repo is a fork (could be a modified clone of the real project)")
    if meta.get("archived"):
        add(-10, "archived (no longer maintained)")
    if pushed is None:
        add(-5, "last push date unknown")
    else:
        idle = (now - pushed).days
        if idle > 730:
            add(-10, f"no updates for {idle // 365}+ years")
        elif idle < 90:
            add(5, "recently updated")
    if meta.get("license"):
        add(5, "has a license")
    else:
        add(-5, "no license")
    if has_releases:
        add(5, "publishes releases")
    if not has_readme:
        add(-5, "no README")
    if not meta.get("description"):
        add(-5, "no description")
    if (meta.get("owner") or {}).get("type") == "Organization":
        add(5, "owned by an organization")

    name = str(meta.get("name") or "").lower()
    for known in known_names:
        k = known.lower()
        if name and k != name and difflib.SequenceMatcher(None, name, k).ratio() >= 0.85:
            add(-40, f"name is very close to '{known}' (possible typosquat)")
            flags.append(f"possible typosquat of {known}")
            break

    score = max(0, min(100, score))
    verdict = "PROCEED" if score >= 70 else "ASK" if score >= 40 else "STOP"
    if any("typosquat" in f for f in flags):
        verdict = "STOP"
    elif flags and verdict == "PROCEED":
        verdict = "ASK"
    return TrustReport(score, verdict, reasons, flags)
