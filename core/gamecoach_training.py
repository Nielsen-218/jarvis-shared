"""Per-player Nolagin coaching memory: learn from outcomes, not invented game facts.

Only user-reported match outcomes are treated as verified. Existing clip
reviews are secondary hypotheses until corroborated. No paid AI calls here.
Uses Jarvis's person-scoped, atomic storage and keeps bounded session history.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone

CATEGORIES = (
    "positioning", "decision", "healing", "rotation", "teamplay",
    "aim", "crosshair", "movement", "objective", "looting",
)
GAMES = ("apex", "cod")
RESULTS = ("win", "loss", "practice")
STORE = "gaming/coach_outcomes.json"
DRILLS = {
    "positioning": "For three fights, enter with hard cover and a planned escape angle.",
    "decision": "Before a challenge ask: advantage, cover, teammate, exit.",
    "healing": "After taking damage, break sightline before healing or re-peeking.",
    "rotation": "Plan your next safe position before taking the next fight.",
    "teamplay": "Call a target, stay tradeable, and regroup before solo peeking.",
    "aim": "Practice ten controlled target switches; prioritize accurate first shots.",
    "crosshair": "Clear five angles with your crosshair pre-placed at head height.",
    "movement": "Practice peeking and returning to cover without exposing your whole body.",
    "objective": "Play three rounds prioritizing objective time over unnecessary kills.",
    "looting": "Set a short loot timer, grab essentials, then regroup.",
}

def _storage():
    from core import storage
    return storage

def report(game: str, category: str, result: str, note: str = "") -> dict:
    """Persist one explicitly user-confirmed match or practice outcome."""
    if game not in GAMES or category not in CATEGORIES or result not in RESULTS:
        raise ValueError("Unsupported game, focus or match result")
    if not isinstance(note, str):
        raise ValueError("Note must be a string")
    s = _storage()
    rows = s.load(STORE, [])
    if not isinstance(rows, list):
        rows = []
    entry = {
        "game": game, "category": category, "result": result,
        "note": note.strip()[:240],
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "owner_confirmed",
    }
    rows.append(entry)
    s.save(STORE, rows[-400:])
    return entry

def build_profile(game: str, reported: list[dict], reviews: list[dict]) -> dict:
    if game not in GAMES:
        raise ValueError("Unsupported game")
    verified = Counter()
    victories = Counter()
    for row in reported[-400:]:
        if not isinstance(row, dict) or row.get("game") != game or row.get("source") != "owner_confirmed":
            continue
        category, result = row.get("category"), row.get("result")
        if category not in CATEGORIES or result not in RESULTS:
            continue
        if result in ("loss", "practice"):
            verified[category] += 1
        elif result == "win":
            victories[category] += 1
    # Reviewing a clip is informative, but an LLM's interpretation is not
    # first-hand confirmed evidence. It never becomes a verified loss.
    estimated = Counter()
    for review in reviews[-100:]:
        if not isinstance(review, dict):
            continue
        # Older clip reviews were Apex-only and have no game field.
        if review.get("game", "apex") != game:
            continue
        points = review.get("weak_points", [])
        if not isinstance(points, list):
            continue
        for item in points:
            if isinstance(item, dict) and item.get("category") in CATEGORIES:
                estimated[item["category"]] += 1
    weighted: dict[str, float] = {}
    for category in CATEGORIES:
        failures = verified[category]
        samples = failures + victories[category]
        # Primary signal: repeated verified outcomes. Secondary signal:
        # multiple AI-reviewed clips can nudge, but do not dominate.
        confirmed = (failures / samples) if samples >= 3 else 0.0
        hypothesis = min(1.0, estimated[category] / 5) * 0.2
        weighted[category] = round(min(.4, max(0., confirmed * .4 + hypothesis)), 3)
    focus = sorted(
        CATEGORIES,
        key=lambda c: (weighted[c], verified[c], estimated[c], c),
        reverse=True,
    )
    enough = bool(sum(verified.values()) + sum(victories.values()) >= 3 or
                  sum(estimated.values()) >= 3)
    primary = focus[0] if enough and weighted[focus[0]] > 0 else None
    return {
        "game": game,
        "confirmed_reports": sum(verified.values()) + sum(victories.values()),
        "review_hypotheses": sum(estimated.values()),
        "verified_issues": dict(verified),
        "verified_wins": dict(victories),
        "estimated_issues": dict(estimated),
        "weights": weighted,
        "primary_focus": primary,
        "drill": DRILLS[primary] if primary else
                 "Play three matches and label one decision that won or lost each fight.",
    }

def current_profile(game: str) -> dict:
    s = _storage()
    reports = s.load(STORE, [])
    reviews = s.load("gaming/reviews.json", [])
    return build_profile(game, reports if isinstance(reports, list) else [],
                         reviews if isinstance(reviews, list) else [])

def training_plan(game: str) -> str:
    profile = current_profile(game)
    focus = profile["primary_focus"]
    if not focus:
        return ("I don't have enough verified matches yet to identify your play style. "
                "After each game, tell me one winning or losing decision. "
                f"First drill: {profile['drill']}")
    confidence = "confirmed match reports" if profile["confirmed_reports"] >= 3 else "unverified clip reviews"
    return (f"Focus for your next {game.upper()} matches: {focus}. "
            f"Based on {confidence}; not a proven causal weakness. "
            f"Drill: {profile['drill']} "
            "Give me your result after the match so I can adjust.")
