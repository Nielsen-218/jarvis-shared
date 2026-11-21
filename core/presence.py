"""Living Presence Engine — who owns the companion's position, what the user is doing, and where he rests.

Pure: no Win32, no pywebview and no clock of its own (callers pass `now`), so every decision is unit-tested.
hud/app.py feeds it real desktop geometry; app/companion/interaction.js runs the matching pointer-side state
machine and reports leases here through the window's Api.

The old rule moved him whenever the cursor touched his window, so he fled exactly when the user reached for him.
Now a direct interaction (hover, press, drag, throw, menu, game, skill) LEASES his position and placement waits.
With no lease he moves only when he is really in the way — over the active window's content or the text being
typed — and only to a spot that is clearly better (a weighted cost with hysteresis), never just because the
cursor came near.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from core.companion import Monitor

# ---------------------------------------------------------------- interaction states
# Interaction states, not emotions or animations: he can be happy while hovered or tired while docked.
INTERACTION_STATES = ("ambient", "curious", "hovered", "engaged", "menu_open", "held", "thrown", "drop_target",
                      "playing", "skill_running", "work_focus", "repositioning", "docked", "suspended")
# While one of these holds the lease, the user (or a running interaction) owns where he is.
OWNING_STATES = frozenset({"hovered", "engaged", "menu_open", "held", "thrown", "drop_target", "playing",
                           "skill_running", "repositioning"})
LEASE_MAX_TTL = 10.0     # s: a lease that isn't renewed lapses (a crashed or reloaded page can't freeze placement)
SETTLE_MAX = 30.0


class Lease:
    """Position ownership. The renderer grants a lease when an interaction starts and renews it while it lasts.
    Releasing may leave a settle period: after a throw lands he gets a moment before placement judges the spot."""

    def __init__(self) -> None:
        self.state: str | None = None
        self.until = 0.0
        self.settle_until = 0.0

    def grant(self, state: str, ttl: float, now: float) -> bool:
        if state not in OWNING_STATES:
            return False
        self.state = state
        self.until = now + max(0.2, min(LEASE_MAX_TTL, float(ttl)))
        return True

    def release(self, state: str | None, settle: float, now: float) -> None:
        if state is None or state == self.state:       # an old release must not end a newer interaction
            self.state, self.until = None, 0.0
        self.settle_until = max(self.settle_until, now + max(0.0, min(SETTLE_MAX, float(settle))))

    def holder(self, now: float) -> str | None:
        return self.state if self.state and now < self.until else None

    def blocks(self, now: float) -> bool:
        return self.holder(now) is not None or now < self.settle_until

    def snapshot(self, now: float) -> dict:
        return {"holder": self.holder(now), "settling": max(0.0, round(self.settle_until - now, 2))}


# ---------------------------------------------------------------- what the user is doing
ACTIVITIES = ("other", "coding", "typing", "reading", "browsing", "video", "meeting", "gaming", "idle", "away")
# Attention belongs elsewhere: he stays calm and in place (see the renderer's activity policy).
FOCUSED_ACTIVITIES = frozenset({"coding", "typing", "reading", "video", "meeting"})

_EDITORS = {"code.exe", "code - insiders.exe", "cursor.exe", "devenv.exe", "pycharm64.exe", "idea64.exe",
            "webstorm64.exe", "rider64.exe", "clion64.exe", "goland64.exe", "sublime_text.exe", "notepad++.exe",
            "notepad.exe", "zed.exe", "windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
            "openconsole.exe", "mintty.exe", "blender.exe", "unrealeditor.exe", "unity.exe", "claude.exe"}
_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe"}
_VIDEO = {"vlc.exe", "mpc-hc64.exe", "mpc-hc.exe", "potplayermini64.exe", "wmplayer.exe", "video.ui.exe",
          "mpv.exe", "plex.exe", "jellyfin media player.exe", "jellyfinmediaplayer.exe"}
_MEETINGS = {"zoom.exe", "teams.exe", "ms-teams.exe", "webex.exe", "ciscowebexstart.exe"}
_DOCUMENTS = {"winword.exe", "excel.exe", "powerpnt.exe", "onenote.exe", "acrobat.exe", "acrord32.exe",
              "sumatrapdf.exe", "obsidian.exe", "notion.exe"}
_VIDEO_TITLE = re.compile(r"youtube|netflix|twitch|prime video|disney\+|hulu|jellyfin|plex|vimeo|crunchyroll", re.I)


def classify_activity(*, process: str = "", title: str = "", typing: bool = False, idle_seconds: float = 0.0,
                      fullscreen: bool = False, gaming: bool = False) -> str:
    """What the user is doing, from cheap facts: the foreground app's executable and title, whether keys are
    being pressed, how long since any input, and whether a full-screen app is up."""
    p = (process or "").lower()
    if gaming or (fullscreen and p not in _BROWSERS | _VIDEO):
        return "gaming"
    if idle_seconds >= 300:
        return "away"
    if idle_seconds >= 60:
        return "idle"
    if p in _VIDEO or (p in _BROWSERS and _VIDEO_TITLE.search(title or "") and not typing):
        return "video"
    if p in _MEETINGS:
        return "meeting"
    if p in _EDITORS:
        return "coding"
    if p in _DOCUMENTS:
        return "typing" if typing else "reading"
    if p in _BROWSERS:
        return "typing" if typing else "browsing"
    return "typing" if typing else "other"


# ---------------------------------------------------------------- where to rest
PLACE_MARGIN = 12
PLACE_COOLDOWN = 6.0        # s between automatic moves
PLACE_HYSTERESIS = 15.0     # a new spot must be this much cheaper to be worth the trip (no jitter between equals)
PLACE_OBSTRUCTED = 22.0     # below this he isn't in anyone's way: he stays, wherever the cursor is
CARET_OBSTRUCTED = 12.0     # ...unless he sits right where text is being typed
PLACE_CROWDED = 45.0        # even the best spot costs this much: dock (smaller, translucent)
PLACE_PEEK = 80.0           # nothing is free at all: collapse to a peek at the screen edge
UNDOCK_BELOW = 20.0         # docked, and a spot this good opens up: come back out
MANUAL_RESPECT = 45.0       # s after the user puts him somewhere: only a serious obstruction moves him
SEVERE = 60.0

W_COVER, W_OTHERS, W_CARET, W_CURSOR, W_MOVE, W_MONITOR, W_PREF = 100.0, 12.0, 40.0, 14.0, 10.0, 10.0, 8.0
KIND_BONUS = {"corner": -3.0, "taskbar": -2.0, "preferred": -2.0, "edge": -1.0, "beside": -1.0, "current": 0.0}
CARET_RADIUS, CURSOR_RADIUS = 260.0, 200.0
REACH_RADIUS = 220.0        # the mouse moving this close to him: the user may be reaching for him — he waits
MAXIMIZED_FRAC = 0.85       # a foreground window covering this share of its monitor is treated as maximized


@dataclass(frozen=True)
class Scene:
    """Everything placement looks at, in one coordinate space (physical screen pixels)."""
    monitors: tuple
    size: tuple                        # his window (w, h)
    current: tuple | None = None       # his window's top-left now
    foreground: tuple | None = None    # the active window (x, y, w, h)
    others: tuple = ()                 # other visible app windows (x, y, w, h)
    caret: tuple | None = None         # the text caret (x, y), when the app exposes it
    cursor: tuple | None = None
    user_active: bool = False          # the user gave input in the last few seconds
    preferred: tuple | None = None     # where the user last put him by hand
    pointing: bool = False             # the mouse itself moved in the last couple of seconds


@dataclass(frozen=True)
class Placement:
    target: tuple | None               # move here, or None to stay
    reason: str
    cost_here: float = 0.0
    cost_best: float = 0.0
    style: str = "walk"                # walk | hop | fly | teleport
    dock: int = 0                      # 0 normal, 1 compact + translucent, 2 peek at the screen edge
    breakdown: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"target": list(self.target) if self.target else None, "reason": self.reason,
                "cost_here": round(self.cost_here, 1), "cost_best": round(self.cost_best, 1),
                "style": self.style, "dock": self.dock, "breakdown": self.breakdown}


def _overlap(ax, ay, aw, ah, bx, by, bw, bh) -> int:
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))


def _monitor_of(px, py, monitors) -> int | None:
    for i, m in enumerate(monitors):
        if m.x <= px < m.right and m.y <= py < m.bottom:
            return i
    if not monitors:
        return None
    return min(range(len(monitors)), key=lambda i: abs(monitors[i].x + monitors[i].width / 2 - px)
               + abs(monitors[i].y + monitors[i].height / 2 - py))


def _inside(x, y, w, h, monitors, inset: int = PLACE_MARGIN // 2) -> bool:
    """Fully on some work area, a few px in from its edges (no spot glued to the screen border)."""
    return any(m.x + inset <= x and m.y + inset <= y and x + w <= m.right - inset and y + h <= m.bottom - inset
               for m in monitors)


def is_maximized(foreground, monitors) -> bool:
    if not foreground:
        return False
    fx, fy, fw, fh = foreground
    return any(_overlap(fx, fy, fw, fh, m.x, m.y, m.width, m.height) >= MAXIMIZED_FRAC * m.width * m.height
               for m in monitors)


def candidate_spots(monitors, w: int, h: int, *, foreground=None, preferred=None, current=None,
                    margin: int = PLACE_MARGIN) -> list[tuple[int, int, str]]:
    """Places he could rest: every monitor's corners, points along the taskbar edge and the other edges, just
    outside each side of the active window, the user's own spot and where he is now. Only spots fully inside a
    work area are offered (clamping a "beside the window" spot would put him back on the window)."""
    raw = []
    for m in monitors:
        left, right = m.x + margin, m.right - w - margin
        top, bottom = m.y + margin, m.bottom - h - margin
        raw += [(left, top, "corner"), (right, top, "corner"), (left, bottom, "corner"), (right, bottom, "corner")]
        for frac in (0.25, 0.5, 0.75):
            x = m.x + int((m.width - w) * frac)
            raw += [(x, bottom, "taskbar"), (x, top, "edge")]
        for frac in (0.33, 0.66):
            y = m.y + int((m.height - h) * frac)
            raw += [(left, y, "edge"), (right, y, "edge")]
    if foreground:
        fx, fy, fw, fh = foreground
        g = margin
        raw += [(fx - w - g, fy + fh - h, "beside"), (fx + fw + g, fy + fh - h, "beside"),
                (fx - w - g, fy, "beside"), (fx + fw + g, fy, "beside"),
                (fx + fw - w, fy + fh + g, "beside"), (fx, fy + fh + g, "beside"), (fx + fw - w, fy - h - g, "beside")]
    if preferred:
        raw.append((preferred[0], preferred[1], "preferred"))
    seen, out = set(), []
    for x, y, kind in raw:
        x, y = int(x), int(y)
        if (x, y) in seen or not _inside(x, y, w, h, monitors):
            continue
        seen.add((x, y))
        out.append((x, y, kind))
    if current and (int(current[0]), int(current[1])) not in seen:
        out.append((int(current[0]), int(current[1]), "current"))     # where he is, even if half off-screen (peek)
    return out


def content_importance(u: float, v: float, maximized: bool) -> float:
    """How much a point of the active window (u, v in 0..1 across it) matters. A normal window is all content.
    A maximized one fills the screen, so he has to sit on it somewhere: its middle and its top band (tabs, menus,
    the minimize/maximize/close buttons) matter most, its bottom edge (status bar) and side margins least."""
    if not maximized:
        return 1.0
    centre = math.exp(-(((u - 0.5) ** 2) / 0.08 + ((v - 0.5) ** 2) / 0.12))
    top = 1.0 if v < 0.10 else 0.0
    controls = 1.0 if (u > 0.82 and v < 0.10) else 0.0
    return 0.25 + 0.75 * centre + 0.6 * top + 0.8 * controls


def _near(px, py, qx, qy, radius) -> float:
    d = math.hypot(px - qx, py - qy)
    return max(0.0, 1.0 - d / radius)


def spot_cost(x: int, y: int, kind: str, scene: Scene) -> dict:
    """The weighted cost of resting at (x, y), with its parts (for diagnostics and tests)."""
    w, h = scene.size
    area = max(1, w * h)
    cx, cy = x + w / 2, y + h / 2
    parts = {"cover": 0.0, "others": 0.0, "caret": 0.0, "cursor": 0.0, "move": 0.0, "monitor": 0.0, "pref": 0.0,
             "kind": KIND_BONUS.get(kind, 0.0)}
    fg = scene.foreground
    if fg:
        fx, fy, fw, fh = fg
        ov = _overlap(x, y, w, h, fx, fy, fw, fh)
        if ov:
            u = min(1.0, max(0.0, (cx - fx) / max(1, fw)))
            v = min(1.0, max(0.0, (cy - fy) / max(1, fh)))
            parts["cover"] = W_COVER * (ov / area) * content_importance(u, v, is_maximized(fg, scene.monitors))
    if scene.others:
        ov = sum(_overlap(x, y, w, h, *r) for r in scene.others)
        parts["others"] = W_OTHERS * min(1.0, ov / area)
    if scene.caret:
        parts["caret"] = W_CARET * _near(cx, cy, scene.caret[0], scene.caret[1], CARET_RADIUS) ** 2
    if scene.cursor and scene.user_active:
        parts["cursor"] = W_CURSOR * _near(cx, cy, scene.cursor[0], scene.cursor[1], CURSOR_RADIUS) ** 2
    mons = scene.monitors
    diag = math.hypot(*_desktop_extent(mons)) or 1.0
    if scene.current:
        ox, oy = scene.current
        parts["move"] = W_MOVE * math.hypot(x - ox, y - oy) / diag
        if _monitor_of(cx, cy, mons) != _monitor_of(ox + w / 2, oy + h / 2, mons):
            parts["monitor"] = W_MONITOR
    if scene.preferred:
        parts["pref"] = W_PREF * math.hypot(x - scene.preferred[0], y - scene.preferred[1]) / diag
    parts["total"] = sum(v for k, v in parts.items())
    return parts


def _desktop_extent(monitors) -> tuple[float, float]:
    if not monitors:
        return (0.0, 0.0)
    return (max(m.right for m in monitors) - min(m.x for m in monitors),
            max(m.bottom for m in monitors) - min(m.y for m in monitors))


def travel_style(current, target, monitors, reduced_motion: bool = False) -> str:
    """How he gets there: a calm walk across a short stretch, hops for a longer one, flight across the screen,
    and a teleport between monitors or when motion is reduced."""
    if reduced_motion or not current:
        return "teleport"
    d = math.hypot(target[0] - current[0], target[1] - current[1])
    same = _monitor_of(current[0], current[1], monitors) == _monitor_of(target[0], target[1], monitors)
    if not same:
        return "teleport" if d > 2600 else "fly"
    if d <= 420:
        return "walk"
    if d <= 1100:
        return "hop"
    return "fly"


def plan_placement(scene: Scene, *, now: float, last_move: float = 0.0, lease_blocks: bool = False,
                   manual_until: float = 0.0, docked: int = 0, reduced_motion: bool = False) -> Placement:
    """Stay, or move to the best clear spot. Order of reasons: an interaction owns him (lease) -> stay; the mouse
    is moving close to him (the user may be reaching for him) and he isn't over the text being typed -> stay; he
    isn't in the way -> stay (the cursor alone never counts); just moved -> wait; the user placed him recently and
    the obstruction isn't serious -> stay; nothing is clearly better -> stay; else move, docking when even the best
    spot is crowded."""
    if not scene.monitors or not scene.current:
        return Placement(None, "no-geometry")
    if lease_blocks:
        return Placement(None, "interaction")
    w, h = scene.size
    here = spot_cost(scene.current[0], scene.current[1], "current", scene)
    here_xy = (int(scene.current[0]), int(scene.current[1]))
    spots = [(x, y, k) for (x, y, k) in candidate_spots(scene.monitors, w, h, foreground=scene.foreground,
                                                        preferred=scene.preferred, current=scene.current)
             if k != "current" and (x, y) != here_xy]
    if not spots:
        return Placement(None, "no-spots", here["total"])
    scored = sorted(((spot_cost(x, y, k, scene), (x, y)) for (x, y, k) in spots), key=lambda it: it[0]["total"])
    best, at = scored[0]
    if scene.pointing and scene.cursor and here["caret"] < CARET_OBSTRUCTED:
        d = math.hypot(scene.cursor[0] - (here_xy[0] + w / 2), scene.cursor[1] - (here_xy[1] + h / 2))
        if d <= max(REACH_RADIUS, 1.2 * max(w, h)):
            return Placement(None, "reaching", here["total"], best["total"])
    if docked and best["total"] < UNDOCK_BELOW:
        return Placement(at, "undock", here["total"], best["total"],
                         travel_style(scene.current, at, scene.monitors, reduced_motion), 0, _round(best))
    obstructed = here["cover"] >= PLACE_OBSTRUCTED or here["caret"] >= CARET_OBSTRUCTED
    if not obstructed:
        return Placement(None, "clear", here["total"], best["total"], breakdown=_round(here))
    if now - last_move < PLACE_COOLDOWN:
        return Placement(None, "cooldown", here["total"], best["total"])
    if now < manual_until and here["cover"] < SEVERE:
        return Placement(None, "manual", here["total"], best["total"])
    if here["total"] - best["total"] < PLACE_HYSTERESIS:
        return Placement(None, "no-better-spot", here["total"], best["total"], breakdown=_round(here))
    dock = 2 if best["total"] >= PLACE_PEEK else 1 if best["total"] >= PLACE_CROWDED else 0
    target = peek_spot(at, scene) if dock == 2 else at
    return Placement(target, "obstructed", here["total"], best["total"],
                     travel_style(scene.current, target, scene.monitors, reduced_motion), dock, _round(best))


def best_spot(scene: Scene) -> tuple[int, int] | None:
    """The cheapest place to rest other than where he is (an explicit "come back out" ignores hysteresis)."""
    if not scene.monitors:
        return None
    w, h = scene.size
    here = (int(scene.current[0]), int(scene.current[1])) if scene.current else None
    spots = [(x, y, k) for (x, y, k) in candidate_spots(scene.monitors, w, h, foreground=scene.foreground,
                                                        preferred=scene.preferred) if (x, y) != here]
    if not spots:
        return None
    x, y, _ = min(spots, key=lambda s: spot_cost(s[0], s[1], s[2], scene)["total"])
    return (x, y)


def peek_spot(at, scene: Scene) -> tuple[int, int]:
    """Nowhere is free: tuck him mostly past the nearest side edge of his monitor so only a sliver shows."""
    w, h = scene.size
    m = scene.monitors[_monitor_of(at[0] + w / 2, at[1] + h / 2, scene.monitors) or 0]
    left = (at[0] + w / 2) < (m.x + m.width / 2)
    x = m.x - int(w * 0.55) if left else m.right - int(w * 0.45)
    return (x, max(m.y, min(at[1], m.bottom - h)))


def _round(parts: dict) -> dict:
    return {k: round(v, 1) for k, v in parts.items() if v}


# ---------------------------------------------------------------- the menu beside him
def expand_layout(robot, panel, monitors, *, gap: int = 6, prefer: str = "") -> dict:
    """Room for the companion menu without moving him: the panel opens on the side of his monitor with space
    (`prefer` when it fits), bottom level with his feet, nudged to stay on screen; the window grows to hold both.
    robot = his window (x, y, w, h); panel = (w, h). Returns the side, the new window (x, y, w, h) and the robot
    and panel boxes RELATIVE to that window."""
    rx, ry, rw, rh = (int(v) for v in robot)
    pw, ph = int(panel[0]), int(panel[1])
    mons = list(monitors) or [Monitor(rx - 2000, ry - 2000, 4000 + rw, 4000 + rh)]
    m = mons[_monitor_of(rx + rw / 2, ry + rh / 2, mons) or 0]
    room = {"right": m.right - (rx + rw), "left": rx - m.x}
    if prefer in room and room[prefer] >= pw + gap:
        side = prefer
    else:
        side = "right" if room["right"] >= room["left"] else "left"
    px = rx + rw + gap if side == "right" else rx - gap - pw
    px = max(m.x, min(px, m.right - pw))
    py = max(m.y, min(ry + rh - ph, m.bottom - ph))
    wx, wy = min(rx, px), min(ry, py)
    wr, wb = max(rx + rw, px + pw), max(ry + rh, py + ph)
    return {"side": side, "window": [wx, wy, wr - wx, wb - wy],
            "robot": [rx - wx, ry - wy, rw, rh], "panel": [px - wx, py - wy, pw, ph]}


__all__ = [
    "INTERACTION_STATES", "OWNING_STATES", "Lease", "ACTIVITIES", "FOCUSED_ACTIVITIES", "classify_activity",
    "Scene", "Placement", "candidate_spots", "content_importance", "spot_cost", "plan_placement", "travel_style",
    "best_spot", "peek_spot", "is_maximized", "expand_layout",
]
