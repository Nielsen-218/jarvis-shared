"""Living Desktop Companion — behavior model, event contract, and settings.

The visual rendering lives in app/companion.html (three.js concept robot (a skinned
Hunyuan3D GLB) with a 2D concept-sprite fallback); this module owns everything that can be exercised
without a browser: the versioned event schema the window honors, the idle
action picker with weighted cooldowns, work-area position clamping, and
persistent settings/position storage. Keep this file pure so tests can
construct it without pywebview or an audio stream.

Event contract (v1):
    {"type": "state",   "value": "idle|listening|thinking|speaking|working|muted|error"}
    {"type": "level",   "value": float in [0, 1]}            # TTS amplitude 0..1
    {"type": "line",    "who": "you|jarvis|sys", "text": str}
    {"type": "settings","value": {...}}                       # see Settings
    {"type": "position","value": {"x": int, "y": int}}        # window placement
    {"type": "idle",    "value": "<action name>"}             # ambient action nudge

Any event with an unknown type, missing required field, or out-of-range value
is dropped by validate_event(); the window applies only events this function
returns truthy for, so a bad producer cannot wedge the renderer.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EVENT_SCHEMA_VERSION = 1

# States the behavior layer accepts. "idle" is the baseline; "sleeping" is an
# alias kept for existing core/hud.py callers. The main loop in main.py is the
# authoritative producer of state; the resolver's only job is to reject
# gibberish, not to reorder transitions the main loop sends.
STATES: tuple[str, ...] = (
    "idle", "sleeping", "listening", "thinking", "speaking", "working", "muted", "error",
)
STATE_ALIASES = {"sleeping": "idle"}

# Ambient states are those idle actions originate from; application states
# are the rest. The renderer uses this split to decide when to run idle
# animations, NOT to filter transitions — the main loop is the one source of
# truth, and any state it emits is accepted.
AMBIENT_STATES = frozenset({"idle"})

# Idle actions (visibly distinct ambient behaviors). Each entry gives a
# relative selection weight, a per-action cooldown in seconds, and a
# "big_gesture" flag that reduced-motion suppresses.
IDLE_ACTIONS: dict[str, dict[str, Any]] = {
    "breathe":     {"weight": 10.0, "cooldown": 1.5,  "big_gesture": False},
    "blink":       {"weight":  8.0, "cooldown": 2.0,  "big_gesture": False},
    "glance":      {"weight":  4.0, "cooldown": 7.0,  "big_gesture": False},
    "weight_shift":{"weight":  2.0, "cooldown": 14.0, "big_gesture": True},
    "look_around": {"weight":  3.0, "cooldown": 10.0, "big_gesture": False},
    "ponder":      {"weight":  1.5, "cooldown": 25.0, "big_gesture": False},
    "stretch":     {"weight":  1.0, "cooldown": 45.0, "big_gesture": True},
    "yawn":        {"weight":  0.8, "cooldown": 60.0, "big_gesture": True},
    "wander":      {"weight":  0.6, "cooldown": 50.0, "big_gesture": True},
    "sit":         {"weight":  1.0, "cooldown": 40.0, "big_gesture": True},
    "perch":       {"weight":  0.7, "cooldown": 70.0, "big_gesture": True},
    "curious":     {"weight":  1.2, "cooldown": 30.0, "big_gesture": True},
    "laugh":       {"weight":  0.6, "cooldown": 55.0, "big_gesture": True},
    "fly":         {"weight":  0.5, "cooldown": 80.0, "big_gesture": True},
    "parkour":     {"weight":  0.5, "cooldown": 90.0, "big_gesture": True},
    "play":        {"weight":  0.8, "cooldown": 45.0, "big_gesture": True},
    "wave":        {"weight":  0.5, "cooldown": 90.0, "big_gesture": True},
    "dance":       {"weight":  0.4, "cooldown": 120.0, "big_gesture": True},
    # Calm, in-place pastimes for when the user is busy (Living Presence: personality while working).
    "read":        {"weight":  0.7, "cooldown": 50.0, "big_gesture": False},
    "hologram":    {"weight":  0.7, "cooldown": 50.0, "big_gesture": False},
    "salute": {"weight": 0.8, "cooldown": 65.0, "big_gesture": True},
    "double_take": {"weight": 1.0, "cooldown": 45.0, "big_gesture": True},
    "shuffle": {"weight": 0.9, "cooldown": 75.0, "big_gesture": True},
}

# Emotion catalogue (beyond the conversational states). The renderer owns the decaying
# MoodModel (app/companion/behavior.js); this list mirrors it so the `mood` control event
# validates and Jarvis can nudge a feeling after good/bad news. "neutral" is the resting
# baseline and cannot be nudged directly.
MOODS: tuple[str, ...] = ("neutral", "happy", "proud", "playful", "curious", "tired", "annoyed")

# Default settings exposed to the renderer. Bounds matter: safe_settings()
# clamps everything so a bad disk file can't make us render at 240 fps or at
# a negative scale.
DEFAULTS: dict[str, Any] = {
    "quality":        "medium",        # low | medium | high
    "scale":          1.0,             # 0.5 .. 2.0
    "idle_fps":       20,              # 5 .. 60
    "active_fps":     30,              # 10 .. 60
    "reduced_motion": False,
    "dpr_cap":        1.5,             # 1.0 .. 2.0
    "character_px":   160,             # on-screen height of the character, 50 .. 400
    "window_size":    [187, 187],      # legacy [160..640]; the window is derived from character_px
    "defaults_rev":   2,               # which generation of defaults a saved file was written under
    "visible":        True,
    "auto_place":     True,            # let him move to a quiet corner out of the user's way
    "app_reactions":  True,            # run over and kick a newly launched app window (Feature #151)
    "seasonal":       True,            # auto-wear a seasonal accessory + greeting by the calendar
    "proactive":      True,            # time-of-day greetings + a break reminder after long activity
    "launch_drops":   True,            # open a shortcut dropped ON him by its real path (feature #152, native drop)
    "work_aware":     True,            # calm, in-place behaviour while the user types, codes, reads or watches
    "hover_greet":    True,            # a small hello when the cursor settles on him
}

# What the user is doing (core.presence.classify_activity) and how a relocation travels; mirrored here so the
# renderer's events validate without importing the presence engine.
ACTIVITIES = ("other", "coding", "typing", "reading", "browsing", "video", "meeting", "gaming", "idle", "away")
ROBOT_ACTIONS = ("home", "sleep", "wake", "cook", "workshop", "exercise", "read", "pet",
                 "taskbar", "search", "folder", "window", "favorite", "leave", "jetpack", "dance",
                 "catch", "juggle", "kick", "quiet", "desktop")

TRAVEL_STYLES = ("walk", "hop", "fly", "teleport")

QUALITY_LEVELS = ("low", "medium", "high")

# companion.html frames the whole rig (fins, claws, feet) with headroom (fitCameraToRig). Measured on the
# real window (183 px window -> 157 px silhouette), so the window is the character's height times this.
FIT_MARGIN = 1.166
CHARACTER_PX_RANGE = (50, 400)


def window_for_character(character_px: int) -> tuple[int, int]:
    """Square window edge that shows a character_px-tall companion with its whole rig in frame."""
    px = max(CHARACTER_PX_RANGE[0], min(CHARACTER_PX_RANGE[1], int(character_px)))
    edge = int(round(px * FIT_MARGIN))
    return edge, edge

# Bounded queue sizes. The renderer collapses rapid updates to the newest
# value for coalescible types (level, state), so the queue only needs to
# hold a short burst of commands.
MAX_LEVEL_QUEUE = 64
MAX_EVENT_QUEUE = 256


# ---------------------------- event validation ---------------------------- #

def validate_event(msg: Any) -> dict | None:
    """Return a normalized event dict, or None if the input is unsafe to apply.

    This is intentionally defensive: core/hud.py pumps from main.py's loop at
    ~14 Hz, and a malformed amplitude ("NaN", "", "-10") must never reach the
    renderer because the mouth morph would behave unpredictably. Known types
    are listed in EVENT_SCHEMA_VERSION 1; new ones require bumping the
    version so the renderer can refuse to apply unknown shapes.
    """
    if not isinstance(msg, dict):
        return None
    t = msg.get("type")
    if not isinstance(t, str):
        return None

    if t == "state":
        v = msg.get("value")
        if not isinstance(v, str):
            return None
        v = STATE_ALIASES.get(v, v)
        if v not in STATES:
            return None
        return {"type": "state", "value": v}

    if t == "level":
        v = msg.get("value")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        if fv != fv:            # NaN
            return None
        if fv < 0:
            fv = 0.0
        if fv > 1:
            fv = 1.0
        out = {"type": "level", "value": round(fv, 3)}
        src = msg.get("source")
        if isinstance(src, str) and src[:32]:
            out["source"] = src[:32]
        return out

    if t == "line":
        who = msg.get("who")
        text = msg.get("text")
        if who not in ("you", "jarvis", "sys") or not isinstance(text, str):
            return None
        return {"type": "line", "who": who, "text": text[:400]}

    if t == "settings":
        v = msg.get("value")
        if not isinstance(v, dict):
            return None
        return {"type": "settings", "value": safe_settings(v)}

    if t == "position":
        v = msg.get("value")
        if not isinstance(v, dict):
            return None
        try:
            x = int(v.get("x"))
            y = int(v.get("y"))
        except (TypeError, ValueError):
            return None
        return {"type": "position", "value": {"x": x, "y": y}}

    if t == "idle":
        v = msg.get("value")
        if v not in IDLE_ACTIONS:
            return None
        return {"type": "idle", "value": v}

    if t == "throw":
        # Launch the companion with a release velocity (px/s). Lets Jarvis throw
        # himself (a celebration, a game, "toss yourself over there") and makes
        # the throw path drivable/testable. Clamped so a bad value can't fling
        # him at absurd speed.
        try:
            vx = float(msg.get("vx", 0))
            vy = float(msg.get("vy", 0))
        except (TypeError, ValueError):
            return None
        if vx != vx or vy != vy:            # NaN
            return None
        clamp = lambda n: max(-6000.0, min(6000.0, n))
        return {"type": "throw", "vx": round(clamp(vx), 1), "vy": round(clamp(vy), 1)}

    if t == "celebrate":
        return {"type": "celebrate"}   # victory hop (game wins, good news)

    if t == "doing":
        v = msg.get("value")                 # what a tool is busy with ("opening Spotify"); "" = finished
        if not isinstance(v, str):
            return None
        return {"type": "doing", "value": " ".join(v.split())[:120]}

    if t == "doze":
        return {"type": "doze", "on": bool(msg.get("on", True))}   # sleep / wake the companion

    if t == "proactive":
        # Proactive assistance: fire a time-of-day greeting and/or exercise the break-reminder path.
        out = {"type": "proactive", "greet": bool(msg.get("greet")), "longsession": bool(msg.get("longsession"))}
        if "hour" in msg:
            try:
                out["hour"] = max(0, min(23, int(msg["hour"])))
            except (TypeError, ValueError):
                pass
        return out

    if t == "mood":
        # Nudge an emotion (Jarvis after good/bad news, or tests). Decaying mood biases idle behaviour.
        value = msg.get("value")
        if value not in MOODS or value == "neutral":
            return None
        try:
            amount = float(msg.get("amount", 0.6))
        except (TypeError, ValueError):
            amount = 0.6
        return {"type": "mood", "value": value, "amount": max(0.0, min(1.0, amount))}

    if t == "drop":
        # Simulate a file dropped on him (the real path is the page's HTML5 drop handler;
        # this lets Jarvis/tests trigger the catch animation). `name` is cosmetic.
        name = msg.get("name")
        return {"type": "drop", "name": str(name)[:180] if isinstance(name, str) else "file"}

    if t == "appreact":
        # Run over to a newly launched app window and kick it (Feature #151).
        target = msg.get("target")
        if not (isinstance(target, (list, tuple)) and len(target) == 2):
            return None
        try:
            tx, ty = int(target[0]), int(target[1])
        except (TypeError, ValueError):
            return None
        return {"type": "appreact", "target": [tx, ty], "comedy": bool(msg.get("comedy"))}

    if t == "activity":
        # What the user is doing (Living Presence): the renderer keeps calm and in place while they work.
        v = msg.get("value")
        return {"type": "activity", "value": v} if v in ACTIVITIES else None

    if t == "relocate":
        # Placement decided he's in the way: the renderer animates the trip (walk / hop / fly / teleport).
        try:
            x, y = int(msg.get("x")), int(msg.get("y"))
        except (TypeError, ValueError):
            return None
        style = msg.get("style") if msg.get("style") in TRAVEL_STYLES else "walk"
        try:
            dock = max(0, min(2, int(msg.get("dock", 0))))
        except (TypeError, ValueError):
            dock = 0
        return {"type": "relocate", "x": x, "y": y, "style": style, "dock": dock}

    if t == "companion_action":
        return {"type":t,"action":msg.get("action")} if msg.get("action") in ROBOT_ACTIONS else None

    if t == "habitat":
        action = msg.get("action", "favorite")
        return {"type": "habitat", "action": action if action in ("favorite", "taskbar", "search", "folder", "window", "leave", "detach", "save") else "favorite"}

    if t == "menu":
        # Open / close / toggle his interaction menu (the Ctrl+Alt+K hotkey, or tests).
        action = msg.get("action", "toggle")
        view = msg.get("view")
        out = {"type": "menu", "action": action if action in ("open", "close", "toggle") else "toggle"}
        if isinstance(view, str) and view in ("main", "talk", "skills", "play", "settings", "home", "habitat"):
            out["view"] = view
        return out

    if t == "game":
        # A panel mini-game started / ended / was won: he watches, then reacts to the real result.
        phase = msg.get("phase")
        if phase not in ("start", "end", "win"):
            return None
        game = msg.get("game")
        return {"type": "game", "phase": phase, "game": str(game)[:40] if isinstance(game, str) else ""}

    # Preserve the existing HUD protocol passthroughs so the companion window
    # stays compatible with the panel protocol (badges, media, notify...).
    # ``companion_show`` / ``companion_hide`` are the independent-visibility
    # events hud/app.py uses to hide the companion without touching the panel.
    if t in ("badges", "media", "media_clear", "mode", "timers", "notify",
             "focus", "show", "hide", "companion_show", "companion_hide",
             "dashboard", "wardrobe", "quit", "companion_toggle", "mini"):
        return dict(msg)
    if t == "companion":                                  # visibility echo for the Command Center's button
        return {"type": "companion", "visible": bool(msg.get("visible"))}
    if t == "__never__":
        return dict(msg)

    return None


def resolve_state(current: str, incoming: str) -> str:
    """Return the next state the renderer should display.

    The main loop (main.py) is the authoritative state producer: it already
    composes muted / speaking / listening / working / idle every ~70 ms and
    sends whatever is true right now. The resolver's job is only to reject
    gibberish — a transition from any valid state to any other valid state
    must be honored, so a listening→speaking→idle lifecycle and an
    error→muted→listening recovery flow pass through untouched. Earlier
    revisions had a priority table here that made higher-priority states
    sticky and broke speaking→idle; that is gone on purpose.
    """
    current_norm = STATE_ALIASES.get(current, current)
    if current_norm not in STATES:
        current_norm = "idle"
    incoming_norm = STATE_ALIASES.get(incoming, incoming)
    if incoming_norm not in STATES:
        return current_norm
    return incoming_norm


def is_ambient(state: str) -> bool:
    """True when idle animations are allowed to drive the character."""
    return STATE_ALIASES.get(state, state) in AMBIENT_STATES


# ---------------------------- idle action picker ---------------------------- #

@dataclass
class IdlePicker:
    """Weighted-random idle action selector with per-action cooldowns.

    The renderer calls choose(now) a few times a second while idle; it either
    returns an action name whose cooldown has elapsed, or None (meaning
    "keep breathing"). Reduced motion suppresses big gestures.
    """
    reduced_motion: bool = False
    _last: dict[str, float] = field(default_factory=dict)
    _rng: random.Random = field(default_factory=random.Random)

    def choose(self, now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else float(now)
        candidates: list[tuple[str, float]] = []
        for name, spec in IDLE_ACTIONS.items():
            if self.reduced_motion and spec["big_gesture"]:
                continue
            if now - self._last.get(name, -1e9) < spec["cooldown"]:
                continue
            candidates.append((name, spec["weight"]))
        if not candidates:
            return None
        total = sum(w for _, w in candidates)
        r = self._rng.random() * total
        acc = 0.0
        for name, w in candidates:
            acc += w
            if r <= acc:
                self._last[name] = now
                return name
        name = candidates[-1][0]
        self._last[name] = now
        return name

    def reset(self) -> None:
        self._last.clear()


# ---------------------------- position clamp ---------------------------- #

@dataclass(frozen=True)
class Monitor:
    """A monitor's work area (physical pixels, with origin at the virtual
    desktop origin — so x/y may be negative when a secondary monitor is to
    the left of the primary)."""
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def clamp_position(x: int, y: int, w: int, h: int, monitors: list[Monitor]) -> tuple[int, int]:
    """Keep the companion inside *some* monitor's extents.

    PARTIAL: these are monitor extents (webview.Screen.width/height), NOT
    the Windows work area. The real work area (SystemParametersInfo
    SPI_GETWORKAREA, per-monitor DPI) is not implemented in this slice;
    see the status table in DEVELOPMENT-HANDOFF.md. In practice this means
    the companion can be dragged under the taskbar until a Windows-API
    pass lands.

    Picks the monitor the window's center currently overlaps most; if the
    window is entirely off every monitor (e.g. the user unplugged the display
    the window was on), it snaps to the primary monitor (monitors[0]). The
    returned position guarantees `x` and `y` are integers and the window's
    body stays fully on-screen (not merely a single pixel on-screen).
    """
    if not monitors:
        return int(x), int(y)
    try:
        x = int(x); y = int(y); w = max(1, int(w)); h = max(1, int(h))
    except (TypeError, ValueError):
        x = y = 0; w = h = 320

    cx, cy = x + w // 2, y + h // 2
    def overlap(m: Monitor) -> int:
        return int(m.x <= cx < m.right and m.y <= cy < m.bottom)
    chosen = max(monitors, key=overlap)
    if not overlap(chosen):
        chosen = monitors[0]
    w = min(w, chosen.width)
    h = min(h, chosen.height)
    x = max(chosen.x, min(x, chosen.right - w))
    y = max(chosen.y, min(y, chosen.bottom - h))
    return x, y


# ---------------------------- smart positioning ---------------------------- #
# "He must never become an obstacle to the user's work" (companion directive,
# Part III): the companion rests in a quiet corner and scoots aside when a window
# lands over him or the user works right where he sits. This is the PURE geometry;
# the Win32 polling that feeds it — real monitor work areas (taskbar excluded),
# the foreground window rect and the cursor — lives in hud/app.py, so this stays
# importable and unit-testable with no desktop. All coordinates are one space
# (physical pixels on the real path); the engine never mixes spaces.

AUTOPLACE_MARGIN = 12            # px gap from the screen edge at a resting spot
AUTOPLACE_COOLDOWN = 2.0         # s between automatic moves (anti-jitter / hysteresis)
AUTOPLACE_MANUAL_HOLD = 20.0     # s to respect a manual drag before auto-placing again


def _overlap_area(ax: int, ay: int, aw: int, ah: int, bx: int, by: int, bw: int, bh: int) -> int:
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy


def _overlap_any(x: int, y: int, w: int, h: int, avoid) -> int:
    return sum(_overlap_area(x, y, w, h, bx, by, bw, bh) for (bx, by, bw, bh) in avoid)


def resting_candidates(monitors: list[Monitor], w: int, h: int, margin: int = AUTOPLACE_MARGIN):
    """Quiet spots to rest in: each monitor's four corners plus its two vertical
    edge mid-points, inset by `margin`. Returns [(x, y, is_corner)], clamped
    on-screen and de-duplicated."""
    raw = []
    for m in monitors:
        left, right = m.x + margin, m.x + m.width - w - margin
        top, bottom = m.y + margin, m.y + m.height - h - margin
        midx = m.x + (m.width - w) // 2
        raw += [(left, top, True), (right, top, True), (left, bottom, True),
                (right, bottom, True), (midx, top, False), (midx, bottom, False)]
    seen: set = set()
    out = []
    for (x, y, corner) in raw:
        cx, cy = clamp_position(x, y, w, h, monitors)
        if (cx, cy) not in seen:
            seen.add((cx, cy))
            out.append((cx, cy, corner))
    return out


# ---------------------------- app-launch reactions ---------------------------- #
# Which top-level windows count as "an app the user just opened" (Feature #151),
# so the companion reacts to launching Notepad but not to tooltips, menus, the
# taskbar or its own windows. Pure so the Win32 polling in hud/app.py is testable.
SHELL_WINDOW_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow_Hidden",
    "DV2ControlHost", "MSCTFIME UI", "Default IME", "Button", "ForegroundStaging",
})


def is_app_window(*, visible, title, tool_window, has_owner, hwnd=0, classname="", exclude_hwnds=()):
    """True for a normal, alt-tab-able application window. Mirrors the standard
    alt-tab filter: visible, titled, not a tool window, not owned, not a shell
    surface, and not one of ours."""
    if not visible or hwnd in exclude_hwnds:
        return False
    if not isinstance(title, str) or not title.strip():
        return False
    if tool_window or has_owner:
        return False
    if classname in SHELL_WINDOW_CLASSES:
        return False
    return True


def _point_monitor(px, py, monitors: list[Monitor]):
    for i, m in enumerate(monitors):
        if m.x <= px < m.right and m.y <= py < m.bottom:
            return i
    return None


def best_resting_spot(w: int, h: int, monitors: list[Monitor], avoid, cursor, prefer_point=None):
    """The clear resting spot that is least covered, then on the preferred monitor
    (keeps him on the screen he's already on instead of jumping displays), then
    farthest from the cursor, then a true corner. Returns (x, y) or None."""
    pref = _point_monitor(prefer_point[0], prefer_point[1], monitors) if prefer_point else None
    best = None
    best_key = None
    for (x, y, corner) in resting_candidates(monitors, w, h):
        ov = _overlap_any(x, y, w, h, avoid)
        scx, scy = x + w / 2, y + h / 2
        on_pref = 1 if (pref is not None and _point_monitor(scx, scy, monitors) == pref) else 0
        if cursor:
            cdist = ((scx - cursor[0]) ** 2 + (scy - cursor[1]) ** 2) ** 0.5
        else:
            cdist = 0.0
        key = (-ov, on_pref, cdist, 1 if corner else 0)
        if best_key is None or key > best_key:
            best, best_key = (x, y), key
    return best


LAUNCHABLE_DROP_EXTS = (".lnk", ".url")   # shortcuts the user drops ON the companion to open (feature #152)


def classify_dropped_path(path: str) -> str:
    """Pure. What to do with a file the user dropped on the companion, given its full path (which a
    NATIVE Win32 drop recovers, unlike the browser's HTML5 drop). A shortcut is launched by its exact
    path; anything else is saved (the existing catch-and-keep behaviour). Only shortcuts launch, so a
    stray drop can't run an arbitrary executable."""
    if not isinstance(path, str) or not path:
        return "save"
    lowered = path.lower()
    return "launch" if lowered.endswith(LAUNCHABLE_DROP_EXTS) else "save"


def is_exclusive_fullscreen(fg_rect, full_monitors, tol: int = 2) -> bool:
    """Pure. True when the foreground window exactly covers a whole monitor — a
    real full-screen app (game, full-screen video), NOT merely maximized.

    `full_monitors` are FULL monitor bounds (rcMonitor), not work areas: a maximized
    window fills only the work area (taskbar still visible) so it won't match, while a
    true full-screen app covers the taskbar too and will. The companion hides over one
    of these so it never floats on top of a game or a movie."""
    if not fg_rect or not full_monitors:
        return False
    fx, fy, fw, fh = fg_rect
    for m in full_monitors:
        mx, my, mw, mh = (m.x, m.y, m.width, m.height) if hasattr(m, "width") else m
        if abs(fx - mx) <= tol and abs(fy - my) <= tol and abs(fw - mw) <= tol and abs(fh - mh) <= tol:
            return True
    return False


def plan_companion_move(*, pos, size, monitors, foreground, cursor, now,
                        last_auto_move: float = 0.0, manual_until: float = 0.0):
    """Pure. Where the companion should move to stay out of the user's way, or None to stay put.

    A thin wrapper over the Living Presence Engine (core.presence.plan_placement): he moves only when he is
    really in the way of the active window's content, to a clearly better spot. The cursor coming near (or
    resting on him) is NOT a reason to move — that used to make him flee exactly when the user reached for him."""
    from core import presence
    scene = presence.Scene(tuple(monitors), tuple(size), current=tuple(pos), foreground=foreground, cursor=cursor)
    return presence.plan_placement(scene, now=now, last_move=last_auto_move, manual_until=manual_until).target


# ---------------------------- settings ---------------------------- #

def safe_settings(raw: Any) -> dict[str, Any]:
    """Merge raw input onto DEFAULTS, clamping every field to safe bounds."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out

    q = raw.get("quality")
    if isinstance(q, str) and q in QUALITY_LEVELS:
        out["quality"] = q

    try:
        s = float(raw.get("scale", out["scale"]))
        out["scale"] = max(0.5, min(2.0, s))
    except (TypeError, ValueError):
        pass

    for key, lo, hi in (("idle_fps", 5, 60), ("active_fps", 10, 60)):
        try:
            v = int(raw.get(key, out[key]))
            out[key] = max(lo, min(hi, v))
        except (TypeError, ValueError):
            pass

    out["reduced_motion"] = bool(raw.get("reduced_motion", out["reduced_motion"]))
    out["visible"] = bool(raw.get("visible", out["visible"]))
    out["auto_place"] = bool(raw.get("auto_place", out["auto_place"]))
    out["app_reactions"] = bool(raw.get("app_reactions", out["app_reactions"]))
    out["seasonal"] = bool(raw.get("seasonal", out["seasonal"]))
    out["proactive"] = bool(raw.get("proactive", out["proactive"]))
    out["launch_drops"] = bool(raw.get("launch_drops", out["launch_drops"]))
    out["work_aware"] = bool(raw.get("work_aware", out["work_aware"]))
    out["hover_greet"] = bool(raw.get("hover_greet", out["hover_greet"]))

    try:
        dpr = float(raw.get("dpr_cap", out["dpr_cap"]))
        out["dpr_cap"] = max(1.0, min(2.0, dpr))
    except (TypeError, ValueError):
        pass

    try:
        out["defaults_rev"] = max(1, min(DEFAULTS_REV, int(raw.get("defaults_rev", 1))))
    except (TypeError, ValueError):
        out["defaults_rev"] = 1

    try:
        cpx = int(raw.get("character_px", out["character_px"]))
        out["character_px"] = max(CHARACTER_PX_RANGE[0], min(CHARACTER_PX_RANGE[1], cpx))
    except (TypeError, ValueError):
        pass

    size = raw.get("window_size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        try:
            w = max(160, min(640, int(size[0])))
            h = max(160, min(640, int(size[1])))
            out["window_size"] = [w, h]
        except (TypeError, ValueError):
            pass

    return out


# Generation 2 (2026-11): he was 50-75 px tall at about 15 frames a second, too small and too choppy to see what
# he does. The defaults became 160 px, 20 fps idle and 30 fps active. A file saved under the old defaults is lifted
# to the new defaults when it is read, but ONLY where it still holds the old default value exactly; any other
# value is the owner's own choice and is kept as it is.
DEFAULTS_REV = 2
_REV2_LIFT = {"character_px": (75, 160), "idle_fps": (15, 20)}    # key: (the old default, the new default)


def upgrade_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Pure: a value that still EQUALS an old default (never chosen by the owner, as far as the file can tell) moves
    to the new default; any other value, smaller included, is his choice and stays. Idempotent even if a writer
    drops defaults_rev: a lifted value no longer equals an old default, so nothing is lifted twice."""
    out = dict(settings)
    if int(out.get("defaults_rev", 1) or 1) < DEFAULTS_REV:
        for key, (old, new) in _REV2_LIFT.items():
            if out.get(key) == old:
                out[key] = new
        out["defaults_rev"] = DEFAULTS_REV
    return out


def data_directory() -> Path:
    """Companion-only state root; native harnesses use an isolated directory."""
    import os
    raw = os.getenv("JARVIS_COMPANION_DATA_DIR")
    return Path(raw).resolve() if raw else Path(__file__).resolve().parent.parent / "data"


def default_settings_path() -> Path:
    return data_directory() / "companion-settings.json"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Read settings from disk, falling back to DEFAULTS on any error.

    A corrupted settings file is a mild annoyance, not a crash; the renderer
    will log and continue with safe defaults.
    """
    p = path or default_settings_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    return upgrade_settings(safe_settings(raw))


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    p = path or default_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(safe_settings(settings), indent=2), encoding="utf-8")


def default_position_path() -> Path:
    return data_directory() / "companion-position.json"


def load_position(path: Path | None = None) -> tuple[int, int] | None:
    p = path or default_position_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return int(raw["x"]), int(raw["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# Where the companion first appears (and where it returns to if its saved position is deleted): the top-left corner
# of the primary screen. Dragging it elsewhere is remembered in data/companion-position.json.
DEFAULT_POSITION = (0, 0)


def save_position(x: int, y: int, path: Path | None = None) -> None:
    p = path or default_position_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")


# ---------------------------- bounded coalescing queue ---------------------------- #

class EventBuffer:
    """A tiny queue that drops the oldest level update when it fills.

    core/hud.py already coalesces level deltas smaller than 0.02; this buffer
    is the renderer-side defense in depth: if the window is paused and
    thousands of frames queue up, we never OOM, we just serve the newest N.
    """

    def __init__(self, limit: int = MAX_EVENT_QUEUE):
        self.limit = int(limit)
        self._q: list[dict] = []

    def put(self, event: dict) -> bool:
        ev = validate_event(event)
        if ev is None:
            return False
        if ev["type"] == "level" and self._q and self._q[-1]["type"] == "level":
            self._q[-1] = ev          # coalesce consecutive level updates
            return True
        self._q.append(ev)
        if len(self._q) > self.limit:
            del self._q[: len(self._q) - self.limit]
        return True

    def drain(self) -> list[dict]:
        out, self._q = self._q, []
        return out

    def __len__(self) -> int:
        return len(self._q)


__all__ = [
    "EVENT_SCHEMA_VERSION", "STATES", "AMBIENT_STATES", "IDLE_ACTIONS", "MOODS", "DEFAULTS",
    "ACTIVITIES", "TRAVEL_STYLES",
    "validate_event", "resolve_state", "is_ambient",
    "IdlePicker",
    "Monitor", "clamp_position",
    "resting_candidates", "best_resting_spot", "plan_companion_move",
    "is_app_window", "SHELL_WINDOW_CLASSES", "is_exclusive_fullscreen",
    "classify_dropped_path", "LAUNCHABLE_DROP_EXTS",
    "AUTOPLACE_MARGIN", "AUTOPLACE_MANUAL_HOLD", "AUTOPLACE_COOLDOWN",
    "FIT_MARGIN", "window_for_character", "safe_settings", "load_settings", "save_settings",
    "load_position", "save_position", "DEFAULT_POSITION",
    "default_settings_path", "default_position_path",
    "EventBuffer",
]
