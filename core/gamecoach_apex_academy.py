"""Version-aware Apex coaching knowledge, reviewed from public reference pages.

This is tactical KNOWLEDGE, not a perception system. Sources prove general
game mechanics; they never prove any event in the player's live match.
YouTube channels/playlists are a research queue, not videos we claim to have
watched or captions we claim to have parsed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

AS_OF = "2026-10-25"
SOURCES = {
    "ea_beginner": {
        "type": "official", "title": "EA Apex beginner guide",
        "url": "https://help.ea.com/fi/articles/apex-legends/how-to-play/",
        "topics": ("pings", "squad play", "healing", "movement", "ring", "core versus freestyle"),
        "review": "text read", "as_of": AS_OF,
    },
    "ea_squad": {
        "type": "official", "title": "EA: Standing out in the Apex Games",
        "url": "https://www.ea.com/games/apex-legends/apex-legends/news/standing-out-in-the-apex-games",
        "topics": ("team spacing", "pinging", "resetting shields", "avoiding over-looting"),
        "review": "text read", "as_of": AS_OF,
    },
    "ea_marked": {
        "type": "official", "title": "EA: Marked Season patch (Aug 2026)",
        "url": "https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
        "topics": ("energy ammo rework", "map changes", "Bloodhound rework", "loot changes"),
        "review": "text read", "as_of": AS_OF,
    },
    "ea_midseason": {
        "type": "official", "title": "EA: Marked midseason patch (Sept 2026)",
        "url": "https://www.ea.com/games/apex-legends/apex-legends/news/marked-midseason-patch-notes",
        "topics": ("map rotation", "shields", "loot availability", "anti-cheat"),
        "review": "text read", "as_of": AS_OF,
    },
    "ama_rotations": {
        "type": "coach", "title": "Amaa: ranked rotations and positioning",
        "url": "https://boostingxp.com/blog/apex-legends/apex-legends-professional-ranked-guide",
        "topics": ("power positions", "safe routes", "third-party timing", "zone edge"),
        "review": "text read; coaching judgment, not game mechanics", "as_of": AS_OF,
    },
    "district_channel": {
        "type": "video_queue", "title": "District: Apex coach channel",
        "url": "https://www.youtube.com/@TheRealDistrict",
        "topics": ("mechanics", "positioning", "game sense"),
        "review": "public channel metadata only; no individual videos viewed", "as_of": AS_OF,
    },
    "positioning_playlist": {
        "type": "video_queue", "title": "Apex positioning and game-sense playlist",
        "url": "https://www.youtube.com/playlist?list=PL_waWDJmtQZMUmJlxrUG4a98JrPjRWvIx",
        "topics": ("positioning", "angles", "cover", "decision-making"),
        "review": "playlist description only; individual videos not reviewed", "as_of": AS_OF,
    },
    "algs_channel": {
        "type": "video_queue", "title": "Official ALGS tournament VOD channel",
        "url": "https://www.youtube.com/@playapexesports",
        "topics": ("squad communications", "rotations", "fight decisions", "endgame"),
        "review": "channel metadata only; individual match tapes not reviewed", "as_of": AS_OF,
    },
}

@dataclass(frozen=True)
class Play:
    kind: str
    category: str
    message: str
    drill: str
    required: tuple[str, ...]
    blocked: tuple[str, ...]
    priority: int
    sources: tuple[str, ...]
    mode: str = "core"

# ALL preconditions are externally supplied player observations or validated
# replay labels, not invented from public YouTube videos or source metadata.
PLAYS = (
    Play("ring_team_rejoin", "rotation",
         "Ring's moving and you're split. Rejoin your squad before committing to a route.",
         "In three matches, call a regroup point before your first ring rotation.",
         ("ring_closing", "team_split"), ("teammates_knocked",), 6,
         ("ea_beginner", "ea_squad")),
    Play("shield_reset_under_fire", "healing",
         "You're shield-broken in a fight. Break line of sight and reset.",
         "After each verified shield break, return to hard cover before your next peek.",
         ("shield_broken", "fighting"), ("safe_cover_confirmed",), 6,
         ("ea_squad", "ea_beginner")),
    Play("third_party_escape", "decision",
         "Another squad is confirmed. Avoid the middle; find a clean exit.",
         "Review five fights: locate your exit before every re-engagement.",
         ("third_party_confirmed", "fighting"), (), 6,
         ("ama_rotations",)),
    Play("rotate_without_loot", "rotation",
         "Ring's moving; don't extend the loot stop. Rotate together.",
         "Set a short looting checkpoint and move before the ring forces you.",
         ("ring_closing", "looting"), ("fighting",), 5,
         ("ea_squad", "ea_beginner")),
    Play("push_only_together", "teamplay",
         "Enemy knocked, but the squad is split. Regroup before a solo push.",
         "After each knock, check a teammate's position before crossing open space.",
         ("knock_confirmed", "team_split"), (), 5,
         ("ea_squad", "ama_rotations")),
    Play("keep_escape_from_height", "positioning",
         "Height's available, but no safe exit is confirmed. Don't overcommit.",
         "Before taking height, name a retreat path and next ring route.",
         ("height_available", "no_exit_route"), (), 5,
         ("ama_rotations",)),
    Play("heal_then_loot", "healing",
         "Loot can wait. Reset your shields and health before checking boxes.",
         "After a fight, heal and check surroundings before taking extended loot.",
         ("fight_finished", "shield_broken"), ("fighting",), 4,
         ("ea_squad", "ea_beginner")),
    Play("safe_knock_conversion", "teamplay",
         "Knock confirmed, teammates with you. Coordinate the push, don't chase alone.",
         "Call the knock and move with one teammate in your next three fights.",
         ("knock_confirmed", "team_together"), ("third_party_confirmed",), 4,
         ("ea_squad",)),
    Play("ping_before_push", "teamplay",
         "Enemy visible and your squad is nearby. Ping the target before committing.",
         "Ping the intended target before at least five planned squad pushes.",
         ("enemy_visible", "team_together"), ("shield_broken",), 3,
         ("ea_beginner", "ea_squad")),
    Play("use_nade_to_displace", "positioning",
         "Enemy behind cover; grenade available. Consider forcing them to move.",
         "Practice displacement grenades in the Firing Range.",
         ("enemy_behind_cover", "grenade_available"), (), 2,
         ("ea_beginner",)),
)

_ALLOWED_MODES = {"core", "ranked", "unranked", "wildcard", "mixtape"}
def select_plays(facts: Mapping[str, bool], mode: str="core") -> list[Play]:
    """Zero assumptions: every required fact must be explicitly True."""
    if mode not in _ALLOWED_MODES:
        raise ValueError("Unsupported Apex mode")
    if not isinstance(facts,Mapping) or any(type(v) is not bool for v in facts.values()):
        raise ValueError("Only confirmed Boolean facts accepted")
    if mode in {"wildcard","mixtape"}:
        # Core survival/respawn mechanics and loot economies can differ in
        # Freestyle modes; do not reuse Ranked playbooks automatically.
        return []
    options=[
        p for p in PLAYS
        if all(facts.get(name) is True for name in p.required)
        and not any(facts.get(name) is True for name in p.blocked)
    ]
    return sorted(options, key=lambda p:(-p.priority,p.kind))

def advice(facts: Mapping[str,bool],mode:str="core") -> dict:
    selected=select_plays(facts,mode)
    if not selected:
        return {"kind":None,"advice":"Not enough verified context for a winning tactical call.",
                "reason":"No matching reviewed evidence; silence during live gameplay is appropriate.",
                "sources":[]}
    best=selected[0]
    return {"kind":best.kind,"advice":best.message,"drill":best.drill,
            "category":best.category,"sources":[SOURCES[id]["url"] for id in best.sources],
            "evidence":list(best.required),
            "basis":"player-provided facts or human-labeled replay, NOT independently observed gameplay"}

def curriculum(category: str) -> list[dict]:
    return [{"kind":p.kind,"drill":p.drill,"sources":[SOURCES[id]["url"] for id in p.sources]}
            for p in PLAYS if p.category==category]

def source_status()->dict:
    return {"as_of":AS_OF,"reviewed_text_sources":sum(x["review"].startswith("text read") for x in SOURCES.values()),
            "youtube_sources_queued":sum(x["type"]=="video_queue" for x in SOURCES.values()),
            "youtube_videos_watched":0,
            "rule_count":len(PLAYS)}
