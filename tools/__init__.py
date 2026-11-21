"""Jarvis's abilities. Each module registers its tools with tools.registry."""
import importlib

DESK_ONLY = {"apps", "bench", "blenderskills", "skillhost", "computer", "eyes", "gaming", "interface", "media", "pcskills", "screen", "screenread", "security", "worldintel",
             "software", "studio", "system"}
# Every tools/*.py module that registers @tool skills. A module missing here is INVISIBLE to the brain (that's how
# read_screen — the free, local, first-choice screen reader — and the pantry/dressing skills were silently dead).
# tests/test_skills_registry.py fails if a skill module is ever left out again.
ALL = ["apps", "bench", "blender", "rigging", "charjobs", "unreal", "blenderskills", "skillhost", "makechar", "dojo", "pcops", "computer", "dressing", "engineering", "eyes", "finance", "fitness", "gcalendar", "trainer", "pca", "pca_health", "gaming", "homelab",
       "fashion", "interface", "learning", "life", "kitchen", "languages", "langlesson", "media", "memory", "military", "missions", "movies", "notes", "pantry",
       "proactive", "projects", "qbittorrent", "reach", "pcview", "pcskills", "screen", "screenread", "security", "selfinfo",
       "research", "software", "studio", "gamestudio", "study", "system", "taught", "today", "trainingtable", "travel", "tutor", "vault", "voice", "weather",
       "web", "worldintel"]


# Opt-in skill modules: tied to one owner's setup (a home server, an Unreal project, Blender rigging, game coaching,
# a laptop relay). Off unless named in JARVIS_SKILLS_ON (comma list, or "all"). JARVIS_SKILLS_OFF turns any module off.
OPTIONAL = {"unreal", "gamestudio", "homelab", "qbittorrent", "pcview", "missions", "rigging", "charjobs", "makechar",
            "blenderskills", "dojo", "gaming"}


def enabled(name: str) -> bool:
    import os
    on = {s.strip() for s in os.getenv("JARVIS_SKILLS_ON", "").lower().split(",") if s.strip()}
    off = {s.strip() for s in os.getenv("JARVIS_SKILLS_OFF", "").lower().split(",") if s.strip()}
    if name in off:
        return False
    return name not in OPTIONAL or "all" in on or name in on


def load_all(server: bool = False) -> None:
    """Import every tool module so its tools are registered. server=True (the always-on laptop) leaves out the
    desk tools that need this PC's screen, speakers, camera or GPU, and skips any module that can't load."""
    for name in ALL:
        if server and name in DESK_ONLY:
            continue
        if not enabled(name):
            continue
        try:
            importlib.import_module(f"tools.{name}")
        except Exception as e:
            if not server:
                raise
            print(f"[tools] skipped {name} on this machine: {type(e).__name__}: {e}")
