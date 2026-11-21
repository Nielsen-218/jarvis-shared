"""Read-only view of what Windows says is playing (System Media Transport Controls).

Browsers register their audio/video tabs here, so this verifies real PLAYBACK in the user's visible browser, which
loading a page cannot.  No packages, no pairing, no input: it only lists sessions.
"""
import re
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "media_sessions.ps1"
BROWSERS = re.compile(r"edge|chrome|firefox|brave|opera", re.I)


def sessions():
    """[{app, status, title}] for every media session Windows knows (empty off Windows or on any failure)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)],
                             capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3:
            rows.append({"app": parts[0], "status": parts[1], "title": parts[2]})
    return rows


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


def confirm_playing(title, timeout=10.0):
    """Wait for a browser session titled like `title` to report Playing.  Returns facts, never assumes."""
    want, deadline, last = _words(title), time.monotonic() + timeout, []
    while True:
        last = [s for s in sessions() if BROWSERS.search(s["app"])]
        for s in last:
            if want and len(want & _words(s["title"])) >= max(1, len(want) // 2):
                if s["status"] == "Playing":
                    return {"playing": True, "app": s["app"], "title": s["title"]}
        if time.monotonic() >= deadline:
            break
        time.sleep(.6)
    seen = [f"{s['title'][:60]} ({s['status']})" for s in last]
    return {"playing": False, "seen": seen}


CONTROL = Path(__file__).resolve().parents[1] / "scripts" / "media_control.ps1"


def command(name, timeout=6.0):
    """pause / play / next / previous on the session Windows calls current, verified by reading the state back.

    Never presses media keys (those toggle whatever has focus); it asks the media session itself and then checks it."""
    if name not in ("pause", "play", "next", "previous"):
        raise ValueError("Unsupported media command")
    before = sessions()
    if not before:
        return {"verified": False, "reason": "Nothing is registered with Windows as playable media right now."}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(CONTROL), "-Command", name],
                             capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"verified": False, "reason": "Windows media control could not be reached."}
    app, accepted = (out.split("|") + ["", ""])[:2]
    if accepted.lower() == "unsupported":
        return {"verified": False, "app": app, "reason": f"{app or 'That player'} has no {name} item here (a single video, not a playlist or queue)."}
    if accepted.lower() != "true":
        return {"verified": False, "reason": f"{app or 'The media session'} did not accept the {name} command."}
    target = next((s for s in before if s["app"] == app), None) or before[0]
    want = {"pause": "Paused", "play": "Playing"}.get(name)
    deadline = time.monotonic() + timeout
    while True:
        after = next((s for s in sessions() if s["app"] == app), None)
        if after and ((want and after["status"] == want) or (not want and after["title"] != target["title"])):
            return {"verified": True, "app": app, "status": after["status"], "title": after["title"], "previous_title": target["title"]}
        if time.monotonic() >= deadline:
            return {"verified": False, "app": app, "reason": f"Windows accepted {name}, but the state did not change ({after['status'] if after else 'gone'}).",
                    "status": after["status"] if after else None}
        time.sleep(.3)


_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60,
        "ninety": 90, "half a": 0.5, "a couple": 2, "a couple of": 2, "a few": 3}
SEEK = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:please\s+)?(?:can you\s+)?"
    r"(?P<verb>fast[- ]?forward|skip(?: ahead| forward)?|jump(?: ahead| forward| back(?:ward)?)?|go (?:forward|ahead|back)|"
    r"rewind|back ?up|move (?:it )?(?:forward|back))"
    r"(?:\s+(?:the|this|that)?\s*(?:video|song|track|clip|movie|it))?"
    r"(?:\s+(?:by|about|like))?"
    r"(?:\s+(?P<n>\d{1,4}|a couple of|a couple|a few|half a|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|"
    r"twenty|thirty|forty-five|forty|fifty|sixty|ninety)\s*(?P<unit>seconds?|secs?|minutes?|mins?|hours?))?"
    r"(?:\s+(?:in|on)\s+(?:the|this)\s+(?:video|song|track))?[\s.!?]*$", re.I)


def parse_seek(text):
    """('forward'|'back', seconds) for 'fast forward the video by one minute', 'rewind 10 seconds', 'skip ahead'; else None."""
    m = SEEK.match((text or "").strip())
    if not m:
        return None
    verb = m.group("verb").casefold()
    direction = "back" if any(w in verb for w in ("back", "rewind")) else "forward"
    if not m.group("n"):
        if re.sub(r"\s+", " ", verb) == "go ahead":
            return None       # "Sure, go ahead." is permission, not "skip forward 10 seconds" (live 2026-11-15); "go ahead 30 seconds" still seeks
        return direction, 10                                  # 'skip ahead' / 'rewind': a short step, like the players' own
    n = m.group("n").casefold()
    amount = float(n) if n.isdigit() else _NUM.get(n, 1)
    unit = m.group("unit").casefold()
    seconds = amount * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    return direction, int(max(1, min(seconds, 4 * 3600)))


def seek(direction, seconds):
    """Move the current media session forward/back, through Windows' media session (never key presses)."""
    if direction not in ("forward", "back"):
        raise ValueError("Seek must be forward or back")
    if not sessions():
        return {"verified": False, "reason": "Nothing is registered with Windows as playable media right now."}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(CONTROL), "-Command",
                              direction, "-Seconds", str(int(seconds))],
                             capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"verified": False, "reason": "Windows media control could not be reached."}
    parts = (out.split("|") + ["", "", "", ""])[:4]
    app, accepted = parts[0], parts[1].lower()
    if accepted == "unsupported":
        return {"verified": False, "app": app, "reason": f"{app or 'That player'} doesn't let Windows move its position."}
    if accepted != "true":
        return {"verified": False, "app": app, "reason": f"{app or 'The media session'} did not accept the jump."}
    try:
        at = int(parts[3])
    except ValueError:
        at = None
    return {"verified": True, "app": app, "at": at}


def clock(seconds):
    if seconds is None:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
