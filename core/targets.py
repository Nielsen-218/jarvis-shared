"""Which open app did the owner mean? Suggest only; never act on a guess.

Studied 2026-11-08 (docs/plans/build-map-from-research.md): Microsoft UFO2 never lets a model type an app name; code lists the
windows that are actually open and the choice is made from that list. Windows-MCP matches window names fuzzily. JARVIS takes
both ideas, with one rule of its own: a fuzzy match is only ever a QUESTION ("Did you mean ChatGPT?"). It never closes or
minimizes anything by itself. Live 2026-11-08: "Close chat TVT" closed Paint, and "close ChatGPT" failed five times.

Speech recognition mangles names ("chat TVT", "chat GBT", "Cloud" for Claude), so the comparison is on letters only:
'chattvt' against 'chatgpt'.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

# Windows that are not apps the owner works in: the shell, overlays, input helpers and Jarvis himself.
_NOT_APPS = {"explorer.exe", "pythonw.exe", "python.exe", "textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe",
             "startmenuexperiencehost.exe", "lockapp.exe", "nvidia overlay.exe", "razerappengine.exe", "gamebar.exe",
             "widgets.exe", "systemsettingsbroker.exe"}
_HOSTS = {"applicationframehost.exe"}
# Apps whose window belongs to a helper process with another name.
_ALIASES = {"systemsettings": "settings", "steamwebhelper": "steam", "windowsterminal": "terminal", "msedge": "edge",
            "mspaint": "paint", "code": "visual studio code"}                 # Store-app frames: the title names the app, the process does not

_FILLER = set("""a an the to of in on at for and or my me please jarvis hey sir can you could would will just now that this it
close quit exit kill shut end stop minimize minimise hide shrink open launch start show bring switch focus go up down app apps
application window program off away screen front""".split())

SUGGEST_AT = 65.0      # a 0-100 letter similarity worth asking about ('chattvt' vs 'chatgpt' is ~71, 'cloud' vs 'claude' ~73)
MARGIN = 6.0           # two different apps this close together: name neither, list what is open instead


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold())


def _stem(process: str) -> str:
    return os.path.splitext((process or "").casefold())[0]


def open_apps(windows: Optional[List[dict]] = None) -> List[Dict[str, str]]:
    """The apps with a window open right now, one row per app: {"app", "process", "title"}. `app` is the name a close or
    minimize step takes (the catalogue name when the app is in it, else the process name)."""
    if windows is None:
        try:
            from tools import pcops
            windows = pcops.windows()
        except Exception:  # noqa: BLE001  (nothing observed: nothing to suggest)
            return []
    try:
        from tools import apps as catalogue
        by_exe = {os.path.splitext(str(exe).casefold())[0]: name for name, exe in catalogue.BUILTINS.items()
                  if str(exe).casefold().endswith(".exe")}
    except Exception:  # noqa: BLE001
        by_exe = {}
    rows: Dict[str, Dict[str, str]] = {}
    for w in windows or []:
        process, title = str(w.get("process") or ""), str(w.get("title") or "").strip()
        low = process.casefold()
        if not title or low in _NOT_APPS or title.casefold() in ("program manager",):
            continue
        if low in _HOSTS:
            name = title.split(" - ")[-1].strip().casefold()
        else:
            name = _stem(process)
        # Exact names only (mspaint.exe -> paint, a catalogue entry as itself); never a fuzzy catalogue lookup, which
        # could put a different app's name in the question.
        app = _ALIASES.get(name) or by_exe.get(name) or name
        if app and app not in rows:
            rows[app] = {"app": app, "process": process, "title": title}
    return list(rows.values())


def _phrases(said: str) -> List[str]:
    """The owner's content words, and runs of up to three of them joined ('chat', 'tvt', 'chattvt')."""
    words = [w for w in re.findall(r"[a-z0-9]+", (said or "").casefold()) if w not in _FILLER]
    out: List[str] = []
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            joined = "".join(words[i:i + n])
            if len(joined) >= 3 and joined not in out:
                out.append(joined)
    return out


def _labels(row: Dict[str, str]) -> List[str]:
    labels = {_letters(row["app"]), _letters(_stem(row["process"]))}
    for part in re.split(r"\s+[-|–—]\s+", row["title"]):
        labels.add(_letters(part))
    return [label for label in labels if len(label) >= 3]


def score(said: str, row: Dict[str, str]) -> float:
    from rapidfuzz import fuzz
    best = 0.0
    for phrase in _phrases(said):
        for label in _labels(row):
            best = max(best, float(fuzz.ratio(phrase, label)))
    return best


def suggest(said: str, windows: Optional[List[dict]] = None, exclude: str = "") -> Optional[Dict[str, object]]:
    """The one open app the owner most plausibly meant, or None when nothing open is close or two apps are equally close.
    `exclude` is the app that was wrongly picked: never offered back."""
    rows = [r for r in open_apps(windows) if r["app"] != (exclude or "").casefold()]
    ranked = sorted(((score(said, r), r) for r in rows), key=lambda x: -x[0])
    if not ranked or ranked[0][0] < SUGGEST_AT:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MARGIN:
        return None
    top, row = ranked[0]
    return {**row, "score": round(top, 1)}


def display(app: str) -> str:
    """'chatgpt' -> 'ChatGPT' for speech; other names title-cased."""
    special = {"chatgpt": "ChatGPT", "msedge": "Edge", "mspaint": "Paint", "vscode": "VS Code"}
    return special.get((app or "").casefold(), (app or "").title())
