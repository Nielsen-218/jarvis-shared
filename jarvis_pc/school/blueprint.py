"""The master-PC-assistant blueprint: the owner's 26 capability areas and the 10-level mastery ladder, as data.

The owner's standard (2026-11-04): a master PC assistant can OBSERVE, UNDERSTAND, PLAN, EXECUTE, VERIFY and LEARN, on familiar
and unfamiliar tasks alike. This file is the map of that standard. It does not claim anything is mastered: each area shows how
many curriculum subjects exist for it and how many are proven, and an area with no subjects yet says so plainly. Adding a
subject to the curriculum and a line to SUBJECT_AREAS is how an area starts to fill in.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

AREAS: Dict[int, str] = {
    1: "Computer awareness and live screen understanding",
    2: "Mouse, keyboard and window control",
    3: "Application discovery and management",
    4: "In-application control of unfamiliar software",
    5: "Internet navigation and research",
    6: "Software installation and configuration",
    7: "Uninstallation, cleanup and recovery",
    8: "File Explorer and file management",
    9: "Windows administration",
    10: "Command-line mastery",
    11: "Programming and development environments",
    12: "Git, GitHub and project management",
    13: "Troubleshooting and self-repair",
    14: "Cybersecurity and malware defense",
    15: "Networking and remote control",
    16: "Phone, laptop and cross-device control",
    17: "Office and productivity applications",
    18: "Creative software and multimedia",
    19: "Gaming and graphics systems",
    20: "Hardware and peripherals",
    21: "Virtual machines, containers and test environments",
    22: "Planning and long-running execution",
    23: "Autonomous learning and self-improvement",
    24: "Research-to-execution (GitHub link to working install)",
    25: "Reliability, safety and accountability",
    26: "Natural conversation and assistance",
}

# curriculum subject id -> the blueprint areas it exercises
SUBJECT_AREAS: Dict[str, Tuple[int, ...]] = {
    "pc_name": (9,), "pc_user": (9,), "pc_windows_build": (9,), "pc_timezone": (9,), "pc_service_state": (9,),
    "pc_process_running": (1, 9), "pc_date": (10,), "pc_git_version": (12, 3),
    "pc_manufacturer": (20,), "pc_env_var": (9, 10), "pc_port_owner": (15, 13), "pc_process_name": (1, 9), "file_word_count": (8,),
    "app_version": (3,), "app_path": (3, 10), "svc_by_name": (9,), "sec_signature": (14,), "log_error_count": (13,), "log_last_error": (13,),
    "ui_window_title": (1,), "ui_click_button": (2,), "ui_fill_form": (2, 4), "ui_pick_item": (2, 4), "ui_toggle_option": (2, 4),
    "ui_wait_then_go": (2, 4), "ui_hotkey_save": (2,), "ui_read_status": (1, 4), "ui_move_window": (2,), "ui_resize_window": (2,),
    "ui_close_window": (2,), "app_notepad_type_save": (2, 4), "app_notepad_replace_save": (2, 4), "app_notepad_read_count": (1, 4),
    "app_calc_type_save": (17, 4), "app_calc_sum_below": (17, 4),
    "web_local_heading": (5,), "web_local_json_value": (5,), "install_unzip_release": (6,), "uninstall_folder": (7,), "web_find_release": (24, 5), "plan_tidy_and_report": (22, 8), "audit_refused_count": (25,), "audit_total_count": (25,),
    "game_corner": (19, 2), "game_tour": (19, 22),
    "phone_paired_count": (16,), "phone_server_up": (16, 15),
    "gfx_gpu_name": (19, 20), "gfx_driver_version": (19,), "gfx_screen_width": (19, 20),
    "self_mastered_count": (23,), "self_learned_by": (23,), "self_stuck_subject": (23,),
    "conv_casual_name": (26,), "conv_casual_space": (26,), "conv_follow_up": (26,),
    "wsl_distro_name": (21,), "wsl_linux_release": (21, 3), "wsl_linux_kernel": (21,),
    "laptop_hostname": (16,), "laptop_service_state": (16, 9), "laptop_disk_used": (16, 20),
    "media_duration": (18,), "media_sample_rate": (18,), "media_channels": (18,),
    "dev_python_version": (11,), "dev_requirements_count": (11,), "dev_package_name": (11,),
    "git_branch": (12,), "git_commit_count": (12,), "git_last_message": (12,), "git_author": (12,), "git_changed_files": (12, 13),
    "pc_cpu_threads": (20,), "pc_ram": (20,), "pc_disk_free": (20, 9), "pc_disk_total": (20, 9), "pc_ip": (15,),
    "file_list": (8, 10), "file_count": (8,), "file_find_ext": (8,), "file_size": (8,), "file_read": (8,), "file_head": (8,),
    "file_search_text": (8,), "file_hash": (8, 14), "file_newest": (8,), "file_largest": (8,), "file_folder_size": (8,),
    "file_line_count": (8,), "file_find_deep": (8,),
    "job_copy": (8,), "job_make_note": (8,), "job_move_matching": (8,), "job_rename": (8,), "job_zip": (8,), "job_append": (8,),
    "job_old_files": (8,), "job_biggest_folder": (8,),
}

# The owner's final mastery test. `needs` are the areas that must each have at least one proven subject before the level can
# be claimed; a level whose areas have no subjects at all is "not tested yet", never assumed.
LADDER: List[Tuple[int, str, str, Tuple[int, ...]]] = [
    (1, "Basic control", "Open Excel and close Notepad", (2, 3)),
    (2, "App navigation", "Find a setting in an unfamiliar program", (1, 2, 4)),
    (3, "Workflow completion", "Create and save a formatted spreadsheet", (4, 17)),
    (4, "Software management", "Install a GitHub project and verify it runs", (6, 7, 24)),
    (5, "Troubleshooting", "Diagnose and repair a broken application", (13, 9)),
    (6, "Independent research", "Learn a new software workflow from documentation", (5, 23)),
    (7, "Skill composition", "Complete a task requiring several applications", (22, 4)),
    (8, "Autonomous self-improvement", "Recognise a weakness, learn a solution, verify it, retain it", (23, 25)),
    (9, "Remote autonomy", "Complete a multi-step PC task started from the phone", (16, 22)),
    (10, "Expert operator", "Reliably solve unfamiliar, complex computer tasks with minimal intervention", tuple(range(1, 27))),
]

# Where the paid API should go first (owner's priority order)
PRIORITIES: List[Tuple[str, Tuple[int, ...]]] = [
    ("Live screen and UI understanding", (1, 4)),
    ("Reliable computer actions", (2, 3)),
    ("Research and software mastery", (5, 6, 24)),
    ("Troubleshooting and recovery", (13, 7)),
    ("Persistent autonomous learning", (23, 25)),
]


def area_counts(subject_ids: Sequence[str], mastered: Sequence[str]) -> Dict[int, Tuple[int, int]]:
    """{area: (subjects in the curriculum, subjects mastered)} for every one of the 26 areas."""
    have = set(subject_ids)
    done = set(mastered)
    out: Dict[int, Tuple[int, int]] = {a: (0, 0) for a in AREAS}
    for sid, areas in SUBJECT_AREAS.items():
        if sid not in have:
            continue
        for a in areas:
            n, m = out[a]
            out[a] = (n + 1, m + (1 if sid in done else 0))
    return out


def proven(counts: Dict[int, Tuple[int, int]], area: int) -> bool:
    n, m = counts[area]
    return n > 0 and m == n


def ladder_level(counts: Dict[int, Tuple[int, int]]) -> int:
    """Highest level L such that levels 1..L all have every needed area fully proven. 0 = none yet."""
    reached = 0
    for level, _name, _example, needs in LADDER:
        if all(proven(counts, a) for a in needs):
            reached = level
        else:
            break
    return reached


def render(subject_ids: Sequence[str], mastered: Sequence[str]) -> str:
    counts = area_counts(subject_ids, mastered)
    covered = [a for a in AREAS if counts[a][0] > 0]
    proven_n = [a for a in covered if proven(counts, a)]
    lines = [f"Master blueprint: {len(covered)} of 26 areas have lessons; {len(proven_n)} fully proven.",
             f"Mastery ladder: level {ladder_level(counts)} of 10 reached."]
    nxt = next((x for x in LADDER if x[0] == ladder_level(counts) + 1), None)
    if nxt:
        missing = [AREAS[a] for a in nxt[3] if not proven(counts, a)]
        lines.append(f"Next: level {nxt[0]} ({nxt[1]}: {nxt[2]}) needs " + "; ".join(missing[:4]) + ("..." if len(missing) > 4 else ""))
    lines.append("By area (mastered/lessons):")
    for a, name in AREAS.items():
        n, m = counts[a]
        lines.append(f"  {a:>2}. {name}: " + (f"{m}/{n}" if n else "no lessons yet"))
    return "\n".join(lines)
