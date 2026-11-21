"""What the PC looks like right now, in a few lines the local planner can read before it plans.

Read-only and cheap: window titles and process names (tools.pcops), which one is in front, and which catalogue apps are
running. No screenshot, no file contents, and bounded in size so a long window list cannot crowd out the task. The text is
handed to the model as data ("read from the PC"), never as instructions.
"""
from __future__ import annotations

import re

_HIDDEN = {"textinputhost.exe", "applicationframehost.exe", "shellexperiencehost.exe", "searchhost.exe",
           "startmenuexperiencehost.exe", "nvidia overlay.exe"}


def snapshot(limit: int = 12) -> dict:
    out: dict = {"foreground": None, "windows": [], "running": []}
    try:
        from tools import pcops
        rows = [w for w in pcops.windows() if w.get("title") and w.get("process", "").casefold() not in _HIDDEN]
    except Exception:  # noqa: BLE001 - a desktop that cannot be read is reported as such, never guessed
        return out
    for w in rows[:limit]:
        item = {"process": w["process"].removesuffix(".exe"), "title": re.sub(r"\s+", " ", w["title"]).strip()[:60],
                "minimized": bool(w.get("minimized"))}
        out["windows"].append(item)
        if w.get("foreground"):
            out["foreground"] = item
    try:
        from core import pcactions
        procs = {w["process"].casefold() for w in rows}
        out["running"] = sorted(app for app, exe in pcactions.APPS.items() if exe.casefold() in procs)
    except Exception:  # noqa: BLE001
        pass
    return out


def describe(snap: dict | None = None) -> str:
    snap = snap or snapshot()
    if not snap["windows"]:
        return "PC STATE: no visible windows (or the desktop could not be read)."
    front = snap["foreground"]
    lines = ["in front: " + (f"{front['process']} ({front['title']})" if front else "nothing"),
             "open windows: " + "; ".join(f"{w['process']}: {w['title']}" + (" [minimized]" if w["minimized"] else "")
                                          for w in snap["windows"])]
    if snap["running"]:
        lines.append("known apps running: " + ", ".join(snap["running"]))
    return "PC STATE (read just now):\n" + "\n".join(lines)

_NOT_APPS = {"pythonw", "python", "textinputhost", "applicationframehost"}
_NAMES = {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "brave": "Brave", "explorer": "File Explorer",
          "systemsettings": "Settings", "claude": "Claude", "chatgpt": "ChatGPT", "notepad": "Notepad", "mspaint": "Paint",
          "code": "VS Code", "steamwebhelper": "Steam", "steam": "Steam", "discord": "Discord", "spotify": "Spotify",
          "blender": "Blender", "unrealeditor": "Unreal Editor", "calculatorapp": "Calculator", "winword": "Word",
          "excel": "Excel", "powerpnt": "PowerPoint", "windowsterminal": "Terminal", "wt": "Terminal"}
_TITLE_NOISE = re.compile(r"\s+-\s+(?:Personal\s+-\s+)?(?:Microsoft.?\s*Edge|Google Chrome|Mozilla Firefox|Brave|File Explorer)$", re.I)


def spoken(snap: dict | None = None) -> str:
    """The open apps in a sentence a person would say ('In front: Claude. Also open: Edge, File Explorer (Downloads).').
    Jarvis's own windows, the desktop and Windows helpers are left out (owner 2026-11-07: 'he says too much')."""
    snap = snap or snapshot(limit=20)
    seen, others = set(), []

    def name(w):
        proc = w["process"].casefold()
        base = _NAMES.get(proc, w["process"])
        title = _TITLE_NOISE.sub("", w["title"]).strip()
        if proc == "explorer" and title and title.casefold() not in ("file explorer", "home"):
            return f"File Explorer ({title})"
        return base

    front = snap.get("foreground")
    if front and (front["process"].casefold() in _NOT_APPS or front["title"] == "Program Manager"):
        front = None
    for w in snap.get("windows", []):
        if w["process"].casefold() in _NOT_APPS or w["title"] == "Program Manager" or w is front:
            continue
        label = name(w) + (" (minimized)" if w.get("minimized") else "")
        if label not in seen and (not front or label != name(front)):
            seen.add(label)
            others.append(label)
    if not front and not others:
        return "No app windows are open."
    parts = [f"In front: {name(front)}." if front else ""]
    if others:
        parts.append(("Also open: " if front else "Open: ") + ", ".join(others) + ".")
    return " ".join(p for p in parts if p)
