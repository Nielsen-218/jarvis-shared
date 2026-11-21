"""Projects: a hands-on helper for hardware and software builds (the old TV,
homelab changes, wiring a Pi...). Jarvis keeps a log per project - parts,
steps done, problems and fixes, ideas - so it can pick up where you left off
and see the whole picture. It looks at your work (camera or screen) when asked.

Stored per person in data/users/<id>/projects/<name>.json.
"""
import re
from datetime import datetime

from core import storage
from tools.registry import tool

KINDS = ["goal", "part", "step", "problem", "fix", "idea", "measurement", "link"]


def _file(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "project"
    return f"projects/{slug}.json"


@tool(
    "Start (or reopen) a hardware/software project so you can keep its log, e.g. 'old TV Jarvis display'.",
    {"name": {"type": "string"}, "goal": {"type": "string", "description": "What it should do when finished"}},
)
def start_project(name: str, goal: str) -> str:
    data = storage.load(_file(name), None)
    if data:
        data["status"] = "active"
        storage.save(_file(name), data)
        return f"Reopened '{data['name']}'.\n" + project_summary(name)
    storage.save(_file(name), {"name": name, "goal": goal, "status": "active",
                               "started": datetime.now().isoformat(timespec="minutes"), "log": []})
    return f"Started project '{name}'. Goal: {goal}"


@tool(
    "Add to a project's log: a part (with price/where), a step done, a problem, its fix, an idea, a "
    "measurement or a useful link. Log as you go, quietly, so nothing gets lost.",
    {"name": {"type": "string"}, "kind": {"type": "string", "enum": KINDS}, "note": {"type": "string"}},
)
def project_log(name: str, kind: str, note: str) -> str:
    data = storage.load(_file(name), None)
    if data is None:
        return f"No project called '{name}'. Start it with start_project."
    data["log"].append({"t": datetime.now().isoformat(timespec="minutes"), "kind": kind, "note": note})
    storage.save(_file(name), data)
    return f"Logged ({kind})."


@tool(
    "Everything about a project: goal, parts, steps done, open problems and ideas. Use it to pick up "
    "where the user left off or to plan the next step.",
    {"name": {"type": "string"}},
)
def project_summary(name: str) -> str:
    data = storage.load(_file(name), None)
    if data is None:
        return f"No project called '{name}'."
    by = {k: [e for e in data["log"] if e["kind"] == k] for k in KINDS}
    fixed = {e["note"] for e in by["fix"]}
    lines = [f"Project '{data['name']}' ({data['status']}, started {data['started'][:10]}). Goal: {data['goal']}"]
    for kind, title in [("part", "Parts"), ("step", "Done so far"), ("problem", "Problems"),
                        ("fix", "Fixes"), ("measurement", "Measurements"), ("idea", "Ideas"), ("link", "Links")]:
        if by[kind]:
            lines.append(f"{title}: " + "; ".join(e["note"] for e in by[kind][-15:]))
    if by["problem"] and not fixed:
        lines.append("Note: problems logged without a fix yet.")
    return "\n".join(lines)


@tool("List the person's projects and their status.")
def list_projects() -> str:
    folder = storage.user_dir() / "projects"
    if not folder.exists():
        return "No projects yet."
    out = []
    for f in sorted(folder.glob("*.json")):
        d = storage.load(f"projects/{f.name}", {})
        out.append(f"{d.get('name')} - {d.get('status')} - {len(d.get('log', []))} log entries")
    return "\n".join(out) or "No projects yet."


@tool(
    "Mark a project finished (or paused).",
    {"name": {"type": "string"}, "status": {"type": "string", "enum": ["finished", "paused", "active"]}},
)
def set_project_status(name: str, status: str) -> str:
    data = storage.load(_file(name), None)
    if data is None:
        return f"No project called '{name}'."
    data["status"] = status
    storage.save(_file(name), data)
    return f"'{data['name']}' marked {status}."
