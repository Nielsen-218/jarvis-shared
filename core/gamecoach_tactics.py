"""Nolagin tactical decision engine: evidence first, one winning play at a time.

This module never captures pixels, reads game memory, calls a cloud model, or
pretends unimplemented perception exists. Upstream screen/audio detectors must
supply timestamped, corroborated observations. Replay labels are permitted only
in offline scenarios. Missing evidence means silence.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Any, Mapping

from core.gamecoach import Observation

# All language is short, actionable, and deliberately does NOT invent directions,
# precise enemy positions, distances, damage, weapon type or teammates' intentions.
TACTICS: dict[str, tuple[str, int, str]] = {
    # kind -> spoken advice, urgency (1..6), training category
    "break_sightline": ("You're exposed and weak. Break line of sight!", 6, "positioning"),
    "avoid_open_revive": ("Don't revive in the open. Secure cover first.", 6, "teamplay"),
    "avoid_pinch": ("Two visible threats. Break the angle; isolate one.", 6, "decision"),
    "retreat_outnumbered": ("Outnumbered. Back off; take a one-on-one angle.", 5, "decision"),
    "rotate_zone": ("Zone is moving. Disengage and rotate.", 5, "rotation"),
    "play_objective": ("Time is low. Play objective, not the chase.", 5, "objective"),
    "reset_shields": ("You've got cover. Recharge before re-peeking.", 4, "healing"),
    "reload_in_cover": ("Low ammo. Use cover to reload or switch weapons.", 4, "decision"),
    "regroup": ("You're split. Regroup before you challenge.", 4, "teamplay"),
    "convert_knock": ("Knock confirmed. Coordinate the push; don't solo chase.", 3, "teamplay"),
    "take_height": ("High ground is open. Consider taking it.", 2, "positioning"),
    "contested_low_health": ("You're low and an enemy's visible. Break contact—reset.", 5, "decision"),
    "contested_low_shields": ("Enemy in sight. Shields low—avoid the re-peek.", 4, "healing"),
    "contested_low_ammo": ("Enemy in sight, low ammo. Make space or switch weapons.", 4, "decision"),
    "unsafe_revive_threat": ("Teammate down, enemy visible. Secure the fight first.", 4, "teamplay"),
    "ring_split_regroup": ("Ring's moving, squad's split. Regroup as you rotate.", 5, "rotation"),
    "knock_split_discipline": ("Knock confirmed, but you're split. Don't solo chase.", 4, "teamplay"),
}

# Strict field catalog: agents cannot manufacture new tactical predicates.
FIELDS = frozenset({
    "health_low", "shields_low", "ammo_low", "exposed", "cover_visible",
    "enemy_visible", "visible_enemy_count", "visible_allies_count",
    "teammate_down", "teammate_near", "squad_split", "zone_moving",
    "in_combat", "objective_timer_low", "objective_visible",
    "confirmed_enemy_knock", "high_ground_access_visible",
    "enemy_flank_visible",
})

@dataclass(frozen=True)
class Evidence:
    value: Any
    seen_at: float
    confidence: float
    source: str
    proof: str

    def valid(self, now: float, max_age_s: float, allow_labels: bool) -> bool:
        if self.source not in ({"pixels", "audio", "user", "clip_label"} if allow_labels
                               else {"pixels", "audio"}):
            return False
        if not isinstance(self.proof, str) or not self.proof.strip():
            return False
        if not all(isinstance(n, (float, int)) and not isinstance(n, bool)
                   and isfinite(n) for n in (self.seen_at, self.confidence)):
            return False
        if not .85 <= self.confidence <= 1:
            return False
        if not -.03 <= now - self.seen_at <= max_age_s:
            return False
        return type(self.value) is bool or (
            type(self.value) is int and 0 <= self.value <= 9
        )

@dataclass(frozen=True)
class Decision:
    kind: str
    text: str
    priority: int
    category: str
    evidence: tuple[str, ...]
    confidence: float
    captured_at: float
    score: float

class TacticalCoach:
    """Deterministic decision policy. Never automatically acts on game controls.

    Only 2+ individually supported facts justify tactical advice. One urgent,
    actionable choice wins. User's training profile is only a small tie-breaker:
    it cannot manufacture evidence or beat an immediate survival warning.
    """
    def __init__(self, game: str = "apex", max_age_ms: float = 600.,
                 learning_weights: Mapping[str, float] | None = None,
                 allow_replay_labels: bool = False):
        if game not in {"apex", "cod"}:
            raise ValueError("Unsupported game")
        self.game = game
        self.max_age_s = min(max(max_age_ms/1000, .05), .65)
        self.learning_weights = dict(learning_weights or {})
        self.allow_replay_labels = allow_replay_labels

    def choose(self, raw: Mapping[str, Evidence], now: float | None = None) -> Decision | None:
        t = monotonic() if now is None else now
        seen: dict[str, Evidence] = {}
        for key, evidence in raw.items():
            if (key in FIELDS and isinstance(evidence, Evidence) and
                    evidence.valid(t, self.max_age_s, self.allow_replay_labels)):
                seen[key] = evidence

        def yes(key: str) -> bool:
            return key in seen and seen[key].value is True

        def count(key: str, minimum: int) -> bool:
            return key in seen and type(seen[key].value) is int and seen[key].value >= minimum

        candidates: list[tuple[str, tuple[str, ...]]] = []
        def add(kind: str, *keys: str, enabled: bool = True) -> None:
            if enabled and all(k in seen for k in keys):
                candidates.append((kind, keys))

        add("break_sightline", "health_low", "exposed",
            enabled=yes("health_low") and yes("exposed"))
        add("avoid_open_revive", "teammate_down", "exposed",
            enabled=yes("teammate_down") and yes("exposed"))
        add("avoid_pinch", "enemy_flank_visible", "enemy_visible",
            enabled=yes("enemy_flank_visible") and yes("enemy_visible"))
        add("retreat_outnumbered", "visible_enemy_count", "visible_allies_count",
            enabled=count("visible_enemy_count", 2) and
                    seen.get("visible_allies_count", Evidence(None,0,0,"","")).value == 0)
        add("rotate_zone", "zone_moving", "in_combat",
            enabled=yes("zone_moving") and yes("in_combat"))
        add("play_objective", "objective_timer_low", "objective_visible",
            enabled=self.game=="cod" and yes("objective_timer_low") and yes("objective_visible"))
        add("reset_shields", "shields_low", "cover_visible",
            enabled=yes("shields_low") and yes("cover_visible"))
        add("reload_in_cover", "ammo_low", "cover_visible",
            enabled=yes("ammo_low") and yes("cover_visible"))
        add("regroup", "squad_split", "enemy_visible",
            enabled=yes("squad_split") and yes("enemy_visible"))
        add("convert_knock", "confirmed_enemy_knock", "teammate_near",
            enabled=yes("confirmed_enemy_knock") and yes("teammate_near"))
        add("take_height", "high_ground_access_visible", "enemy_visible",
            enabled=yes("high_ground_access_visible") and yes("enemy_visible"))
        add("contested_low_health", "health_low", "enemy_visible",
            enabled=yes("health_low") and yes("enemy_visible"))
        add("contested_low_shields", "shields_low", "enemy_visible",
            enabled=yes("shields_low") and yes("enemy_visible"))
        add("contested_low_ammo", "ammo_low", "enemy_visible",
            enabled=yes("ammo_low") and yes("enemy_visible"))
        add("unsafe_revive_threat", "teammate_down", "enemy_visible",
            enabled=yes("teammate_down") and yes("enemy_visible"))
        if self.game == "apex":
            add("ring_split_regroup", "zone_moving", "squad_split",
                enabled=yes("zone_moving") and yes("squad_split"))
            add("knock_split_discipline", "confirmed_enemy_knock", "squad_split",
                enabled=yes("confirmed_enemy_knock") and yes("squad_split"))

        ranked: list[Decision] = []
        for kind, keys in candidates:
            phrase, priority, category = TACTICS[kind]
            confidence = min(seen[k].confidence for k in keys)
            proof = tuple(f"{k}: {seen[k].proof[:160]}" for k in keys)
            # Weakness priorities from previous verified sessions can only break
            # ties *within one urgency band*, never outweigh a survival call.
            personal = self.learning_weights.get(category, 0)
            try:
                personal = float(personal)
                personal = min(.4, max(-.4, personal)) if isfinite(personal) else 0.
            except (ValueError, TypeError):
                personal = 0.
            score = priority + .4 * confidence + .2 * personal
            ranked.append(Decision(kind, phrase, priority, category, proof,
                                   confidence, min(seen[k].seen_at for k in keys), score))
        return max(ranked, key=lambda d: (d.score, d.kind)) if ranked else None

    def to_observation(self, decision: Decision) -> Observation:
        # Existing LiveCoach policy timestamps and cooldowns still apply.
        return Observation(decision.kind, decision.captured_at, decision.confidence,
                           " | ".join(decision.evidence), "pixels", decision.category)


# A second, immediately usable input path: the player's OWN explicit spoken
# facts, not a pretend computer-vision enemy detector. No external AI or APIs.
def advice_from_player_report(game: str, description: str, now: float | None = None) -> str:
    import re
    if not isinstance(description, str) or len(description) > 800:
        return "Tell me the visible situation briefly: health, cover, enemies and squad."
    t = monotonic() if now is None else now
    s = description.lower()
    patterns = {
        "health_low": r"\b(low (?:hp|health)|one hp|critical health|almost dead)\b",
        "shields_low": r"\b(low shields?|shields? (?:broken|cracked|low))\b",
        "ammo_low": r"\b(low ammo|out of ammo|last (?:mag|bullets?))\b",
        "exposed": r"\b(in the open|i(?:'m| am) exposed|no cover|wide swinging)\b",
        "cover_visible": r"\b(behind cover|have cover|got cover|at cover)\b",
        "enemy_visible": r"\b(i see (?:an? )?enem|i see (?:two|2) |enemy in sight|enemies in sight|two on me)\b",
        "squad_split": r"\b(split from (?:my )?(?:team|squad)|teammates? (?:far|away))\b",
        "teammate_down": r"\b(teammate down|teammate got knocked|ally down)\b",
        "teammate_near": r"\b(teammate (?:next to|beside|with) me|squad with me)\b",
        "zone_moving": r"\b(ring closing|zone closing|storm closing)\b",
        "in_combat": r"\b(fighting|in a fight|mid fight|shooting at me)\b",
        "objective_timer_low": r"\b(?:ten|10|five|5) seconds? (?:left|remaining)|time running out\b",
        "objective_visible": r"\b(objective|capture point|plant site|hardpoint)\b",
        "confirmed_enemy_knock": r"\b(i knocked (?:one|an enemy)|enemy (?:is )?knocked)\b",
        "high_ground_access_visible": r"\b(i see (?:open|accessible) high ground)\b",
    }
    facts = {}
    for name, pattern in patterns.items():
        m = re.search(pattern, s)
        if m:
            facts[name] = Evidence(True, t, .95, "user", m.group())
    # Only count enemies if the player explicitly says two visible or two on me.
    m = re.search(r"\b(i see (?:two|2) (?:enemies|guys|players)|two on me)\b", s)
    if m:
        facts["visible_enemy_count"] = Evidence(2, t, .95, "user", m.group())
        facts["enemy_visible"] = Evidence(True, t, .95, "user", m.group())
    m = re.search(r"\b(i(?:'m| am) alone|solo right now|no teammates? near me)\b", s)
    if m:
        facts["visible_allies_count"] = Evidence(0, t, .95, "user", m.group())
    d = TacticalCoach(game, allow_replay_labels=True).choose(facts, now=t)
    return ("Based on what you reported: " + d.text) if d else (
        "I don't have enough concrete details for a tactical call. "
        "Tell me your health, cover, visible threats and squad position.")
