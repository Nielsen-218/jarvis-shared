"""The HUD window — Jarvis's on-screen companion (runs in its own process; see core/hud.py).

Two UI modes share the same process:

- "panel"     — the original 400x660 frameless on-top panel (hud/index.html).
                Everything about this surface — size, position, tray controls,
                hotkeys, voice flows — is byte-for-byte unchanged from the
                pre-companion code paths. Jarvis sends it JSON messages through a
                multiprocessing queue; the page reaches the local app server
                through Api.call (Python side, so no CORS), which is how it loads
                today's workout, logs sets, asks Jarvis things and gets his voice
                for quips. The Command Center dashboard opens as a further native
                window served by the app server.
- "companion" — the panel AS ABOVE, PLUS a second transparent window serving
                the 3D desktop character (/companion.html: the concept robot, a
                skinned glTF model via the profile's adapter). The two windows live in
                the same process, share one brain / one voice via core.hud's
                single mp.Queue, and have independent visibility
                (companion_show / companion_hide events).

Native transparency on Windows:
    webview.create_window(..., transparent=True, background_color="#000000")
is attempted first, but WebView2's own composition typically still shows a
solid black rectangle. As a belt-and-suspenders fallback we also apply
``WS_EX_LAYERED + LWA_COLORKEY=0x000000`` via ctypes once the window has
loaded. Pixels the renderer clears to pure black become see-through; the
character's blue skin / cyan eyes / dark-navy body stay opaque. The cost is
no alpha blending at character silhouettes (edges are pixel-crisp). The
status table in DEVELOPMENT-HANDOFF.md labels this honestly: color-key
transparency, not alpha compositing.
"""
import ctypes
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 400, 660
MINI_W, MINI_H = 220, 300


# ---------------------------------------------------------------- event routing
# Panel displays: HUD commands, modes, badges, media, notifications...
PANEL_LOCAL = frozenset({
    "mode", "badges", "media", "media_clear", "timers", "notify", "focus", "mini",
})
# Companion displays: amplitude, idle action nudges, renderer settings.
COMPANION_LOCAL = frozenset({"level", "doing", "settings", "idle", "throw", "appreact", "celebrate", "drop", "doze", "proactive", "mood",
                             "activity", "relocate", "menu", "game", "companion_action"})
# Workout, Play, Focus (and the joke and stretch break) are views INSIDE the Command Center: a "mode" event raises that window
# and the page shows the view. The old panel (hud/index.html) opens only with JARVIS_DESKTOP_UI=panel.
# Both surfaces see the state transitions and the chat line.
BOTH_SURFACES = frozenset({"state", "line"})

# Legacy aliases kept so the earlier regression tests still exercise the
# right sets even though the architecture is now additive.
PANEL_FORWARDED = PANEL_LOCAL
COMPANION_LOCAL_FULL = COMPANION_LOCAL | BOTH_SURFACES


# ---------------------------------------------------------------- Api
class Api:
    """Functions the page can call (window.pywebview.api.*)."""

    def __init__(self, base: str = "", token: str = "", mode: str = "panel",
                 shared=None):
        self._window = None            # primary window (panel, always)
        self._companion = None         # companion window (companion mode only)
        self._cc = None                # the Command Center window when it is the main window (else the panel is)
        self._quitting = False
        self._user_hidden = False      # the owner dismissed the companion ("go away"): hidden and suspended until called
        self._dash = None
        self._studio = None            # Character Studio window (Feature #27)
        self._panel = None             # legacy summon_panel ad-hoc panel
        self._screen = None
        self._mode = mode
        self._shared = shared          # mp Manager dict for renderer diagnostics
        self.base, self.token = base, token
        self._manual_until = 0.0       # smart-positioning: respect a manual drag until this time
        self._last_auto_move = 0.0     # smart-positioning: last automatic move (cooldown)
        self._physics_until = 0.0      # throw physics owns the window until this time (auto-place yields)
        self._fs_hidden = False        # companion auto-hidden because a full-screen app is foreground
        self._companion = None         # the companion window (set in run(), used to dispatch catch reactions)
        self._last_native_drop = None  # {paths, results, at} from the native (full-path) file drop
        # Living Presence Engine (core/presence.py): who owns his position, what the user is doing, his menu.
        from core.presence import Lease
        self._lease = Lease()          # an interaction in the page holds this; auto-placement waits for it
        self._activity = "other"
        self._last_plan = None         # the latest placement decision (diagnostics)
        self._last_move_plan = None    # ...and the latest one that actually moved him, with what it saw
        self._webview_failures = None  # {count, kind, reason, at}: his WebView2's GPU/renderer process died
        self._docked = 0               # 0 normal, 1 compact + translucent, 2 peeking from the edge (reported by the page)
        self._preferred = _load_preferred()   # where the user last put him by hand
        self._expanded = None          # the menu layout while his window is grown to hold it
        self._scene_rect = None
        self._scene_mode = None
        self._robot_rect = None        # his own box (screen px) while expanded
        self._expand_dpr = 1.0
        self._companion_title = ""
        self._colorkey = (KEY_RGB[2] << 16) | (KEY_RGB[1] << 8) | KEY_RGB[0]
        self._alpha = 255
        self._yield_until = 0.0
        self._prev_fg = None           # the user's window, given focus back when his menu closes
        self._games = None             # the panel's mini-game list + best scores (reported by the panel)
        self._state_written = 0.0
        self._hitmask = None           # his silhouette as clickable cells (see companion_hitmask)
        self._hitmask_lock = threading.Lock()
        from core.desktop_habitat import ShellProbe
        self._shell_probe = ShellProbe()
        self._shell_snapshot = {"surfaces": [], "protected": []}
        self._habitat = None
        self._surface_contact = None
        self._protected = []
        self._strict_protected = []
        self._habitat_visit_at = 0.0
        self._habitat_trip = 0
        try:
            self._fav_surface = json.loads(_data_file("companion-habitat.json").read_text(encoding="utf-8")).get("surface_id")
        except (OSError, ValueError, AttributeError): self._fav_surface = None

    # ----------------------------- primitives
    def open(self, target: str) -> None:
        if target:
            os.startfile(target)

    def hide(self) -> None:
        """Legacy: hide the primary (panel) window. The companion has its own
        companion_show / companion_hide events on the HUD queue."""
        target = self._classic_target()
        if target:
            target.hide()

    def _classic_target(self):
        """The classic 400 px panel: the main window in panel mode only (under the Command Center there is none)."""
        return self._panel if self._cc is not None else self._window

    def call(self, method: str, path: str, body=None):
        """The page's door to the local Jarvis server: {"ok": bool, "status": int, "data": ...}."""
        if not self.base or not path.startswith("/api/"):
            return {"ok": False, "status": 0, "data": {"detail": "Jarvis server isn't connected yet."}}
        req = urllib.request.Request(self.base + path, method=method.upper(),
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return {"ok": True, "status": r.status, "data": json.loads(r.read() or b"null")}
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read() or b"{}")
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {"detail": data}
            data["detail"] = _plain_http_error(e.code, data.get("detail"))
            return {"ok": False, "status": e.code, "data": data}
        except Exception as e:
            print(f"[hud] call {method} {path}: {type(e).__name__}: {e}")
            return {"ok": False, "status": 0, "data": {"detail": "Jarvis's server isn't answering right now."}}

    def say(self, text: str):
        """Jarvis's own voice for a line; returns a playable URL (or "" when the server is unreachable)."""
        r = self.call("POST", "/api/say", {"text": str(text)[:400]})
        audio = (r.get("data") or {}).get("audio") if r.get("ok") else None
        return self.base + audio if audio else ""

    def mini(self, on: bool) -> None:
        """Shrink to just Jarvis (and back), keeping the bottom-right corner where it was."""
        win = self._classic_target()
        if not win:
            return
        w, h = (MINI_W, MINI_H) if on else (WIDTH, HEIGHT)
        win.resize(w, h)
        if self._screen:
            win.move(self._screen.width - w - 16, self._screen.height - h - 56)

    def dashboard(self) -> None:
        open_dashboard(self)

    def idle_seconds(self) -> float:
        """Seconds since the last keyboard/mouse input anywhere on this PC (break nudges, welcome back)."""
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000)

    # ----------------------------- companion-specific
    def move_to(self, x: int, y: int) -> None:
        import time as _time
        self._habitat = None
        target = self._companion or self._window
        if not target:
            return
        from core.companion import clamp_position, save_position
        from core.presence import MANUAL_RESPECT
        # A user drag is a deliberate placement: it becomes his preferred spot, and auto-placement leaves it
        # alone for a while (only a serious obstruction moves him; core.presence.plan_placement).
        self._manual_until = _time.time() + MANUAL_RESPECT
        monitors = self._enumerate_monitors()
        try:
            w, h = int(target.width or 320), int(target.height or 320)
        except Exception:
            w = h = 320
        cx, cy = clamp_position(int(x), int(y), w, h, monitors)
        try:
            target.move(cx, cy)
        except Exception:
            return
        self._preferred = (cx, cy)
        try:
            save_position(cx, cy)
            _save_preferred(cx, cy)
        except Exception:
            pass

    # ---- Physical interaction (Feature #154: pick up and throw) ----
    def companion_bounds(self, x: int = 0, y: int = 0):
        """Bounds the thrown companion bounces in, size-subtracted (resting window satisfies
        left<=x<=right, top<=y<=bottom). Spans the WHOLE virtual desktop so a hard throw can
        cross to another monitor, with `floors`: one {x0,x1,y} per monitor so he lands on the
        floor of whichever display he's over (Feature #154: throws between monitors)."""
        target = self._companion
        if target is None:
            return None
        try:
            w, h = int(target.width or 320), int(target.height or 320)
        except Exception:
            w = h = 320
        mons = _work_area_monitors() or self._enumerate_monitors()
        if not mons:
            return {"left": 0, "top": 0, "right": max(0, 1920 - w), "bottom": max(0, 1080 - h), "w": w, "h": h, "floors": []}
        left = min(m.x for m in mons)
        top = min(m.y for m in mons)
        right = max(m.right for m in mons) - w
        bottom = max(m.bottom for m in mons) - h
        floors = [{"x0": m.x, "x1": m.right - w, "y": m.bottom - h} for m in sorted(mons, key=lambda m: m.x)]
        return {"left": left, "top": top, "right": right, "bottom": bottom, "w": w, "h": h, "floors": floors}

    def move_companion_fast(self, x: int, y: int) -> None:
        """Reposition the companion during a physics animation: native move, NO disk
        save, and hold auto-placement off for a moment (refreshed each frame)."""
        import time as _time
        target = self._companion
        if target is None:
            return
        self._physics_until = _time.time() + 1.0
        try:
            target.move(int(x), int(y))
        except Exception:
            pass

    def companion_settled(self, x: int, y: int) -> None:
        """A throw has come to rest: persist the landing spot and release the physics hold."""
        from core.companion import save_position
        self._physics_until = 0.0
        try:
            save_position(int(x), int(y))
        except Exception:
            pass

    def cursor_pos(self):
        """Screen cursor position [x, y] in physical pixels, for curiosity behaviours. None off Windows."""
        pt = _cursor_point()
        return list(pt) if pt else None

    # ---- Living Presence Engine (core/presence.py; app/companion/interaction.js) ----
    def interaction_lease(self, state: str, ttl_ms: float = 2500) -> bool:
        """The page's interaction (hover, press, drag, throw, menu, game, skill) owns his position: auto-placement
        waits. Renewed about once a second; lapses by itself if the page stops renewing."""
        import time as _t
        return self._lease.grant(str(state), float(ttl_ms) / 1000.0, _t.monotonic())

    def interaction_release(self, state: str = "", settle_ms: float = 0) -> bool:
        """The interaction ended; placement may judge his spot again after `settle_ms`."""
        import time as _t
        self._lease.release(str(state) or None, float(settle_ms) / 1000.0, _t.monotonic())
        return True

    def pointer_state(self):
        """Where the cursor is and whether a file is being dragged out of Explorer / the desktop (he gets ready to
        catch). The page can't see the cursor over his click-through surroundings, so it asks a few times a second."""
        pt = _cursor_point()
        if not pt:
            return None
        down = _mouse_down()
        return {"x": pt[0], "y": pt[1], "left_down": down, "dragging_file": bool(down and _shell_is_foreground())}

    def _companion_hwnd(self) -> int:
        if self._companion is None:
            return 0
        return _window_hwnd(self._companion, title_hint=self._companion_title)

    def _dispatch(self, msg: dict) -> None:
        c = self._companion
        if c is None:
            return
        try:
            c.evaluate_js(f"window.companion && companion.handle({json.dumps(msg)})")
        except Exception as e:
            print(f"[hud] companion dispatch {msg.get('type')}: {type(e).__name__}: {e}")

    def _reset_page_state(self):
        """A fresh document owns no previous room/menu/physics lease."""
        import time
        self._lease.release(None,0,time.monotonic())
        self._lease.settle_until=0
        self._scene_rect=None;self._scene_mode=None;self._expanded=None;self._robot_rect=None
        self._habitat=None;self._surface_contact=None;self._hitmask=None;self._physics_until=0

    def companion_scene(self, mode: str = "", dpr: float = 1.0):
        """Temporarily give the SAME companion window room for his home/play scene."""
        hwnd=self._companion_hwnd()
        if not hwnd: return False
        self.companion_collapse()
        if not mode:
            if self._scene_rect:
                from core.companion import load_settings,window_for_character,clamp_position
                x,y,w,h=self._scene_rect;nw,nh=window_for_character(load_settings()["character_px"])
                cx,cy=clamp_position(x+(w-nw)//2,y+h-nh,nw,nh,_work_area_monitors() or self._enumerate_monitors())
                _set_window_rect(hwnd,cx,cy,nw,nh)
            self._scene_rect=None; self._scene_mode=None
            return True
        if mode not in ("home", "play", "performance"): return False
        from core.presence import expand_layout
        r=self._scene_rect or _window_rect(hwnd)
        if not r: return False
        if self._scene_rect is None: self._scene_rect=tuple(r)
        self._habitat=None; self._scene_mode=mode
        k=max(.5,min(4.,float(dpr or 1)))
        w,h=int(480*k),int(420*k)
        mons=_work_area_monitors() or self._enumerate_monitors()
        m=next((m for m in mons if m.x<=r[0]<m.right and m.y<=r[1]<m.bottom),mons[0] if mons else None)
        if m:
            w,h=min(w,m.width),min(h,m.height)
            x=max(m.x,min(r[0],m.right-w)); y=max(m.y,min(r[1],m.bottom-h))
        else: x,y=r[:2]
        return _set_window_rect(hwnd,x,y,w,h)

    def companion_life(self, kind: str = "", action=None, surface_id=None):
        from core.companion_life import load_life, record_event, traits, LifeError
        try:
            if kind: return {"ok":True,**record_event(str(kind), action=action, surface_id=surface_id)}
            state=load_life()
            return {"ok":True,"snapshot":state,"traits":traits(state)}
        except (LifeError,OSError,ValueError,TypeError) as e: return {"ok":False,"reason":str(e)}

    def companion_life_ack(self, record):
        from core.companion_life import record_event, LifeError
        if not isinstance(record,dict) or set(record)-{"kind","day","routine_time"}: return False
        try:
            if record.get("kind") not in ("greeting","routine","checkpoint"): return False
            record_event(**record);return True
        except (LifeError,TypeError,OSError): return False

    def companion_director(self, day: str, minute: int):
        from core.companion_life import load_life, director_choice, LifeError
        from core.companion_home import load_home
        import time
        try:
            return {"ok":True, "decision":director_choice(load_life(),time.time(),day=str(day),minute=int(minute),
                home=load_home(),available_surfaces=[s.id for s in self._shell_snapshot["surfaces"]],
                quiet=self._activity in ("coding","typing","reading","video","meeting","gaming"),
                busy=self._lease.blocks(time.monotonic()),reduced_motion=bool(self.load_companion_settings()["reduced_motion"]))}
        except (LifeError,OSError,ValueError,TypeError) as e: return {"ok":False,"reason":str(e)}

    def companion_board(self, value=None):
        from core.companion_board import load_board, save_board
        try: return {"ok":True,"state":save_board(value) if value is not None else load_board()}
        except (ValueError,TypeError,OSError) as e: return {"ok":False,"reason":str(e)}

    def companion_home_state(self, patch=None):
        """Validated local room state; same window, no separate brain or registry."""
        from core.companion_home import load_home, update_home, HomeError
        try:
            state=update_home(patch) if patch is not None else load_home()
            return {"ok":True,"state":state}
        except (HomeError,OSError,TypeError,ValueError) as e:
            return {"ok":False,"reason":str(e)}

    def desktop_surfaces(self):
        """Real shell/window geometry only. No fake search control or OS manipulation."""
        from dataclasses import asdict
        return {"surfaces": [asdict(v) for v in self._shell_snapshot["surfaces"]],
                "current": self._habitat, "favorite": self._fav_surface,
                "uia_age": self._shell_snapshot.get("uia_age"), "error": self._shell_snapshot.get("error")}

    def companion_habitat(self, kind: str = "favorite"):
        from core.desktop_habitat import choose_anchor
        if kind in ("leave","detach"):
            self._surface_contact=None
            self._habitat = None
            self._dispatch({"type": "surface", "value": None})
            if kind == "leave": self.companion_home()
            return {"ok": True}
        if kind == "save":
            if not self._habitat: return {"ok": False, "reason": "Choose a real surface first."}
            self._fav_surface = self._habitat["surface_id"]
            path = _data_file("companion-habitat.json"); path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"surface_id": self._fav_surface}), encoding="utf-8")
            return {"ok": True}
        r = self._robot_rect if self._expanded else _window_rect(self._companion_hwnd())
        if not r: return {"ok": False, "reason": "Companion window unavailable."}
        full = _full_monitors()
        from core.companion import Monitor
        mons = [Monitor(*v) for v in full] or self._enumerate_monitors()
        # Fresh native bars are available even before the optional UIA worker finishes.
        wins = [(h, rect) for h, rect, title in _enum_app_windows({self._companion_hwnd()})]
        self._shell_snapshot = self._shell_probe.snapshot(wins)
        self._protected = self._shell_snapshot["protected"]
        self._strict_protected = self._shell_snapshot.get("strict_protected",[])
        surfaces = self._shell_snapshot["surfaces"]
        from dataclasses import replace
        surfaces = [replace(v,occluders=tuple(rect for h,rect in wins)) if v.kind=="folder" and not v.hwnd else v for v in surfaces]
        blocked = []  # Each surface carries only the windows above its actual owner.
        selected = choose_anchor(surfaces, r[2:], mons, preferred=self._fav_surface,
                                 kind=kind if kind in ("taskbar", "search", "folder", "window") else None, blocked=blocked)
        if not selected: return {"ok": False, "reason": "No visible, safe surface of that kind is available."}
        from core.desktop_habitat import protected_local
        if selected["kind"] == "folder":
            clips=protected_local([selected["rect"]], (selected["x"],selected["y"],*r[2:]))
            selected["clip"]=list(clips[0]) if clips else [0,0,0,0]
        self._surface_contact=None
        self._habitat = selected
        self._habitat_visit_at = __import__("time").monotonic()
        self._dispatch({"type": "relocate", "x": selected["x"], "y": selected["y"], "style": "walk", "dock": 0, "habitat": selected})
        return {"ok": True, "anchor": selected}

    def companion_expand(self, panel_w: float, panel_h: float, prefer: str = "", dpr: float = 1.0, owner: str = "menu"):
        """Room for his menu beside him without moving him (core.presence.expand_layout): the window grows to hold
        both boxes in one SetWindowPos. Sizes come from the page in CSS px and the layout goes back in CSS px."""
        from core.presence import expand_layout
        hwnd = self._companion_hwnd()
        if not hwnd:
            return None
        dpr = max(0.5, min(4.0, float(dpr or 1.0)))
        if self._expanded is None:
            rect = _window_rect(hwnd)
            if not rect:
                return None
            self._robot_rect = tuple(rect)
        lay = expand_layout(self._robot_rect, (int(float(panel_w) * dpr), int(float(panel_h) * dpr)),
                            _work_area_monitors() or self._enumerate_monitors(), prefer=str(prefer or ""))
        _set_window_rect(hwnd, *lay["window"])
        self._expanded, self._expand_dpr = lay, dpr
        self._expand_owner = str(owner or "menu")        # who grew the window: his menu, or the overlay card
        _paint_hitmask(self)                             # the menu takes the mouse too, beside him
        return _css_layout(lay, dpr)

    def companion_collapse(self, owner: str = "") -> bool:
        """His menu closed: the window is just him again, exactly where he stood. With `owner`, only an expansion
        that owner made is undone (the overlay card must not shut the window his menu has just taken over)."""
        if owner and self._expanded is not None and getattr(self, "_expand_owner", "menu") != str(owner):
            return False
        hwnd = self._companion_hwnd()
        if not hwnd or self._expanded is None or not self._robot_rect:
            self._expanded = None
            return False
        _set_window_rect(hwnd, *self._robot_rect)
        self._expanded = None
        _paint_hitmask(self)
        return True

    def companion_focus(self, on: bool = True) -> bool:
        """His menu needs the keyboard (search, typing to him): while it's open the window may take focus, and the
        user's own window gets it back when it closes. Otherwise he never steals focus (created no-activate)."""
        hwnd = self._companion_hwnd()
        if not hwnd or os.name != "nt":
            return False
        user32 = ctypes.windll.user32
        if on:
            fg = user32.GetForegroundWindow()
            if fg and fg != hwnd:
                self._prev_fg = fg
            _set_ex_style(hwnd, remove=_WS_EX_NOACTIVATE)
            if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
                return True
            # Opened from the keyboard shortcut, this process didn't receive the input, so Windows' foreground lock
            # refuses it (the keys would keep going to the user's app). Share the foreground thread's input state
            # for a moment — the standard way round it — then let go.
            return _force_foreground(hwnd, fg)
        _set_ex_style(hwnd, add=_WS_EX_NOACTIVATE)
        prev, self._prev_fg = self._prev_fg, None
        if prev and user32.IsWindow(prev) and user32.GetForegroundWindow() == hwnd:
            user32.SetForegroundWindow(prev)
        return True

    def companion_alpha(self, alpha: float = 1.0) -> bool:
        """Overall opacity (docked = translucent), keeping the colour key that makes his surroundings see-through."""
        hwnd = self._companion_hwnd()
        self._alpha = max(40, min(255, int(round(float(alpha) * 255))))
        return _set_layered(hwnd, self._colorkey, self._alpha)

    def companion_yield(self, seconds: float = 30.0) -> bool:
        """Clicks pass straight through him for a while (the user needs what's under him): WS_EX_TRANSPARENT and a
        faded look, undone automatically; the menu hotkey brings him back at once."""
        import time as _t
        hwnd = self._companion_hwnd()
        if not hwnd:
            return False
        secs = max(5.0, min(300.0, float(seconds)))
        _set_ex_style(hwnd, add=_WS_EX_TRANSPARENT)
        _set_layered(hwnd, self._colorkey, min(self._alpha, 150))   # a ghost of himself; his bubble still legible
        self._yield_until = _t.monotonic() + secs
        _paint_hitmask(self)                             # no hit mask behind his faded look
        threading.Timer(secs + 0.05, self._end_yield).start()
        return True

    def _end_yield(self, force: bool = False) -> None:
        import time as _t
        if not force and _t.monotonic() < self._yield_until - 0.5:
            return                                       # a newer yield is still running
        self._yield_until = 0.0
        hwnd = self._companion_hwnd()
        if hwnd:
            _set_ex_style(hwnd, remove=_WS_EX_TRANSPARENT)
            _set_layered(hwnd, self._colorkey, self._alpha)
            _paint_hitmask(self)

    def companion_hitmask(self, spec=None) -> bool:
        """Make HIM clickable — and only him. The colour key makes Windows treat every pixel of his window as
        see-through for the mouse too (the robot is drawn on WebView2's own layer, which that test never sees),
        so a real click went straight through him. The page sends his silhouette (a grid of cells, shrunk so it
        stays hidden behind him); it's painted into the window's background, and exactly those pixels take the
        mouse. Everything else stays click-through. None = no hit area (mid-throw, mid-kick...)."""
        self._hitmask = spec if isinstance(spec, dict) else None
        return _paint_hitmask(self)

    def companion_dock(self, on: bool = True) -> bool:
        """'Tuck me away' / 'Come back out' from his menu: a relocation the page animates."""
        from core.presence import Scene, best_spot, peek_spot
        hwnd = self._companion_hwnd()
        rect = _window_rect(hwnd) if hwnd else None
        monitors = _work_area_monitors() or self._enumerate_monitors()
        if not rect or not monitors:
            return False
        scene = Scene(tuple(monitors), (rect[2], rect[3]), current=(rect[0], rect[1]), preferred=self._preferred)
        target = peek_spot((rect[0], rect[1]), scene) if on else best_spot(scene)
        if not target:
            return False
        self._dispatch({"type": "relocate", "x": int(target[0]), "y": int(target[1]), "style": "walk", "dock": 2 if on else 0})
        return True

    def companion_home(self) -> bool:
        """'Back to my spot': where the user last put him (or the default corner)."""
        from core.companion import DEFAULT_POSITION, clamp_position
        hwnd = self._companion_hwnd()
        rect = _window_rect(hwnd) if hwnd else None
        if not rect:
            return False
        x, y = self._preferred or DEFAULT_POSITION
        x, y = clamp_position(x, y, rect[2], rect[3], _work_area_monitors() or self._enumerate_monitors())
        from core.presence import travel_style
        style = travel_style((rect[0], rect[1]), (x, y), _work_area_monitors() or self._enumerate_monitors())
        self._dispatch({"type": "relocate", "x": int(x), "y": int(y), "style": style, "dock": 0})
        return True

    def listen(self) -> bool:
        """Talk -> the microphone, exactly like the wake word or Ctrl+Alt+J."""
        r = self.call("POST", "/api/companion/listen", {})
        return bool(r.get("ok"))

    def open_wardrobe(self) -> None:
        _open_page(self, "_wardrobe", "J.A.R.V.I.S. · Wardrobe", "wardrobe.html", 1180, 820)

    def report_games(self, payload_json: str) -> bool:
        """The panel tells us its mini-games and best scores, so his Play menu can show them."""
        try:
            data = json.loads(payload_json)
        except (TypeError, ValueError):
            return False
        games = data.get("games") if isinstance(data, dict) else None
        if not isinstance(games, list):
            return False
        clean = [{k: str(g.get(k, ""))[:80] for k in ("id", "name", "icon", "desc", "best")}
                 for g in games[:40] if isinstance(g, dict) and g.get("id")]
        self._games = {"games": clean}
        return True

    def games(self):
        return self._games

    def play_game(self, game: str) -> bool:
        """His Play menu -> the Command Center's Play view opens that mini-game (the old panel in panel mode)."""
        msg = {"type": "mode", "value": "play", "game": str(game)[:40]}
        w = self._cc if self._cc is not None else self._classic_target()
        if w is None:
            return False
        try:
            w.show(); w.restore()
            w.evaluate_js(f"window.jarvis && jarvis.handle({json.dumps(msg)})")
            return True
        except Exception:
            return False

    def companion_game(self, phase: str, game: str = "") -> None:
        """The panel -> him: a game started / was won / ended (he watches, then reacts to the real result)."""
        from core.companion import validate_event
        ev = validate_event({"type": "game", "phase": str(phase), "game": str(game)})
        if ev:
            self._dispatch(ev)

    def skill_prefs(self) -> dict:
        return _load_skill_prefs()

    def save_skill_prefs(self, prefs) -> dict:
        return _save_skill_prefs(prefs)

    def companion_celebrate(self) -> None:
        """Panel -> companion bridge: make the 3D robot do a victory hop (game wins, Feature #153)."""
        c = self._companion
        if c is None:
            return
        try:
            c.evaluate_js("window.companion && companion.handle({type:'celebrate'})")
        except Exception:
            pass

    def companion_retint(self, materials_json: str) -> None:
        """Character Studio -> companion: recolour the live robot now, no reload (Feature #27)."""
        c = self._companion
        if c is None:
            return
        try:
            payload = json.dumps(str(materials_json))   # pass the JSON string through as a JS string literal
            c.evaluate_js(f"window.companion && companion.retint({payload})")
        except Exception:
            pass

    def open_studio(self) -> None:
        open_studio(self)

    def open_learning(self) -> None:
        _open_page(self, "_learning", "J.A.R.V.I.S. · Teach Jarvis", "learning.html", 1040, 720)

    # ---- Drag files / apps onto him (Feature #152) ----
    def save_dropped_file(self, name: str, data_url: str):
        """A file dropped on the companion is handed to Jarvis: decode the data URL and
        save it under data/dropped/ so a workflow can pick it up. Returns the saved path
        or None. Never writes outside data/dropped/ (name is sanitised)."""
        import base64
        import re as _re
        try:
            safe = _re.sub(r"[^A-Za-z0-9._ -]", "_", str(name or "file")).strip()
            safe = safe.replace("..", "_").lstrip(".") or "file"       # no traversal, no leading dots
            safe = safe[:180]
            m = _re.match(r"^data:([^;,]*)(;base64)?,(.*)$", str(data_url or ""), _re.S)
            if not m:
                return None
            raw = base64.b64decode(m.group(3)) if m.group(2) else _re.sub(r"%([0-9A-Fa-f]{2})", lambda h: chr(int(h.group(1), 16)), m.group(3)).encode("utf-8", "ignore")
            if len(raw) > 32 * 1024 * 1024:          # 32 MB cap
                return None
            dest_dir = _data_file("dropped")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / safe
            i = 1
            while dest.exists():                     # don't clobber
                dest = dest_dir / f"{dest.stem}_{i}{dest.suffix}"
                i += 1
            dest.write_bytes(raw)
            print(f"[hud] caught dropped file -> {dest} ({len(raw)} bytes)")
            return str(dest)
        except Exception as e:
            print(f"[hud] save_dropped_file: {type(e).__name__}: {e}")
            return None

    def _handle_native_drops(self, paths, notify_page: bool = True):
        """Files dropped ON the companion, with their FULL paths (unlike the HTML5 route's name + bytes). A
        shortcut is opened by its exact path (feature #152); anything else is copied to data/dropped/. Launching
        is gated by the `launch_drops` setting. notify_page plays the catch reaction (the page already did when
        the drop reached it). Returns a per-path result list and records it on self._last_native_drop."""
        import os
        import shutil
        import time as _time

        from core.companion import classify_dropped_path
        launch_ok = True
        try:
            launch_ok = bool(self.load_companion_settings().get("launch_drops", True))
        except Exception:
            pass
        results = []
        for p in list(paths or []):
            action = classify_dropped_path(p)
            outcome = action
            try:
                if action == "launch":
                    if launch_ok:
                        os.startfile(p)                       # open the shortcut by its exact path
                        outcome = "launched"
                    else:
                        outcome = "launch_disabled"
                else:
                    dest_dir = _data_file("dropped")
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    name = (os.path.basename(p) or "file")[:180]
                    dest = dest_dir / name
                    i = 1
                    while dest.exists():
                        dest = dest_dir / f"{dest.stem}_{i}{dest.suffix}"
                        i += 1
                    shutil.copyfile(p, dest)
                    outcome = f"saved:{dest}"
            except Exception as e:
                outcome = f"error:{type(e).__name__}"
            results.append({"path": p, "outcome": outcome})
            print(f"[hud] native drop: {p} -> {outcome}")
        if any(r["outcome"] == "launched" or r["outcome"].startswith("saved:") for r in results):
            self.companion_life("drop")
        self._last_native_drop = {"paths": list(paths or []), "results": results, "at": _time.time()}
        try:
            if notify_page and self._companion is not None and paths:
                name = os.path.basename(paths[0])
                self._companion.evaluate_js(
                    f"window.companion && companion.handle({{\"type\":\"drop\",\"name\":{json.dumps(name)}}})")
        except Exception:
            pass
        return results

    def set_character_size(self, px: int, persist: bool = True) -> dict:
        """Resize the companion so the character is px tall on screen. The window
        is re-derived from px and kept bottom-centred on the character's feet."""
        from core.companion import (clamp_position, load_settings, safe_settings,
                                    save_settings, save_position, window_for_character)
        target = self._companion
        settings = safe_settings({**load_settings(), "character_px": px})
        if persist:
            save_settings(settings)
        nw, nh = window_for_character(settings["character_px"])
        if self._scene_mode:
            return settings  # keep the home/play canvas large; owner size applies on exit
        if target is not None and self._expanded is not None and self._robot_rect:
            # his menu is open (Settings' size slider): resize HIS box, feet where they were, and re-lay the menu
            from core.presence import expand_layout
            x, y, ow, oh = self._robot_rect
            cx, cy = clamp_position(x + (ow - nw) // 2, y + (oh - nh), nw, nh, self._enumerate_monitors())
            self._robot_rect = (cx, cy, nw, nh)
            pw, ph = self._expanded["panel"][2], self._expanded["panel"][3]
            lay = expand_layout(self._robot_rect, (pw, ph), _work_area_monitors() or self._enumerate_monitors(),
                                prefer=self._expanded["side"])
            _set_window_rect(self._companion_hwnd(), *lay["window"])
            self._expanded = lay
            self._hitmask = {"panel": bool((self._hitmask or {}).get("panel"))}   # his old outline is the wrong size now
            _paint_hitmask(self)
            if persist:
                save_position(cx, cy)
            return {**settings, "layout": _css_layout(lay, self._expand_dpr)}
        if target is not None:
            try:
                ow, oh = int(target.width), int(target.height)
                x, y = int(target.x), int(target.y)
                cx, cy = clamp_position(x + (ow - nw) // 2, y + (oh - nh), nw, nh, self._enumerate_monitors())
                target.resize(nw, nh)
                target.move(cx, cy)
                if (ow, oh) != (nw, nh):
                    self._hitmask = None                 # the page sends his new outline on its next sample
                    _paint_hitmask(self)
                if persist:
                    save_position(cx, cy)
            except Exception:
                pass
        return settings

    def load_companion_settings(self):
        from core.companion import load_settings
        return load_settings()

    def save_companion_settings(self, values: dict) -> dict:
        from core.companion import load_settings, safe_settings, save_settings
        merged = {**load_settings(), **(values or {})}
        settings = safe_settings(merged)
        save_settings(settings)
        return settings

    def load_companion_profile(self, pid: str = "default"):
        """Cosmetic-only appearance profile. Never carries identity /
        memory / brain state — see core/companion_profile.FORBIDDEN_FIELDS."""
        from core import companion_profile as _cp
        try:
            return _cp.load_profile(pid)
        except Exception:
            return _cp.default_profile()

    def save_companion_profile(self, profile: dict) -> dict:
        from core import companion_profile as _cp
        try:
            path = _cp.save_profile(profile or {})
            return _cp.load_profile(_cp.validate_profile(profile)["id"])
        except _cp.ProfileError as e:
            return {"error": str(e)}

    def list_companion_profiles(self):
        from core import companion_profile as _cp
        return _cp.list_profiles()

    def apply_companion_profile(self, profile: dict) -> dict:
        """Persist an appearance and rebuild only the companion window."""
        from core import companion_profile as _cp
        try:
            normalized = _cp.validate_profile(profile)
            if not _cp.profile_path("previous-companion").exists():
                previous = _cp.load_profile()
                previous.update(id="previous-companion", name="Previous companion (saved)")
                _cp.save_profile(previous)
            normalized["id"] = "default"
            _cp.save_profile(normalized)
            if self._companion is not None:
                self._companion.evaluate_js("location.reload()")
            return _cp.load_profile()
        except _cp.ProfileError as e:
            return {"error": str(e)}

    def summon_panel(self) -> None:
        """Click-on-companion: raise the existing panel. In companion mode
        the panel is already created at boot; this just un-hides it if the
        owner had it hidden."""
        if self._window is not None:
            try:
                self._window.show()
                self._window.restore()
                if self._cc is not None:
                    cc_visibility(self._window, True)
                return
            except Exception:
                pass
        if self._cc is not None:                   # under the Command Center the old panel never opens on its own
            return
        open_panel(self)

    def companion_level(self, value: float, source: str = "panel-quip") -> bool:
        """Panel → companion amplitude bridge.

        Called by hud/index.html's quip player (new Audio()). BroadcastChannel
        can't carry this across the panel's file:// origin and the companion's
        http://127.0.0.1:PORT origin, so the Python side is the transport.
        core.hud.level() applies source arbitration (``panel-quip`` wins
        over ``main`` zero-ticks within a grace window) so the main loop's
        silence doesn't overwrite a live quip.

        A non-zero sample also nudges the companion state to "speaking" if
        no other producer has it there, so the mouth actually opens (the
        renderer gates ``mouthTarget`` on ``mode === "speaking"``). The
        state nudge is a no-op if the mode is already speaking/listening/
        anything non-idle, so a live Jarvis reply still owns the mode.
        """
        from core import hud as _hud
        try:
            fv = float(value)
            src = str(source or "panel-quip")[:32]
            if fv > 0.02 and _hud._state.get("value") in ("idle", "sleeping", None):
                _hud.state("speaking")
            _hud.level(fv, source=src)
            return True
        except Exception:
            return False

    def push_renderer_snapshot(self, snapshot_json: str) -> bool:
        """Renderer periodic push for diagnostics. Writes to the shared
        Manager dict so an outer harness or diagnostic endpoint can read
        the companion's live state (mode, jaw, level, frames, FPS...)."""
        import time as _t
        try:
            data = json.loads(snapshot_json)
            if not isinstance(data, dict):
                return False
            data["received_at_ms"] = int(_t.monotonic() * 1000)
            data["presence"] = self.presence_state()
            data["desktop_habitat"] = self.desktop_surfaces()
            contact=data.get("surface_contact")
            if isinstance(contact,list) and len(contact)==2 and all(isinstance(v,(int,float)) and 0<=v<=1 for v in contact):
                self._surface_contact=tuple(contact)
            if self._shared is not None:
                self._shared["renderer"] = data
                return True
            # The live app has no harness: keep the latest snapshot in data/companion-state.json (at most once a
            # second, written atomically) so its state can be checked without screenshots.
            if _t.monotonic() - self._state_written < 0.9:
                return True
            self._state_written = _t.monotonic()
            path = _data_file("companion-state.json")
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    def presence_state(self) -> dict:
        """The host half of the Living Presence state, for diagnostics."""
        import time as _t
        now = _t.monotonic()
        return {"lease": self._lease.snapshot(now), "activity": self._activity, "plan": self._last_plan,
                "last_move": self._last_move_plan, "docked": self._docked, "expanded": bool(self._expanded), "scene": self._scene_mode,
                "yielding": now < self._yield_until, "preferred": list(self._preferred) if self._preferred else None,
                "last_drop": self._last_native_drop, "webview_failures": self._webview_failures}

    def _enumerate_monitors(self):
        from core.companion import Monitor
        try:
            import webview
            screens = webview.screens or []
        except Exception:
            screens = []
        mons = []
        for s in screens:
            try:
                mons.append(Monitor(int(getattr(s, "x", 0) or 0),
                                    int(getattr(s, "y", 0) or 0),
                                    int(s.width), int(s.height)))
            except Exception:
                pass
        if not mons and self._screen is not None:
            try:
                mons.append(Monitor(0, 0, int(self._screen.width), int(self._screen.height)))
            except Exception:
                pass
        return mons


# ---------------------------------------------------------------- helpers
def open_dashboard(api: Api) -> None:
    try:
        _open_dashboard(api)
    except Exception as e:
        import traceback
        print(f"[hud] dashboard failed: {type(e).__name__}: {e}")
        traceback.print_exc()


def open_studio(api: Api) -> None:
    """Open the Character Studio in its own window (Feature #27)."""
    _open_page(api, "_studio", "J.A.R.V.I.S. · Character Studio", "studio.html", 1040, 720)


def _open_page(api: Api, attr: str, title: str, page: str, width: int, height: int) -> None:
    """One window per page (studio, wardrobe): raise it if it's already open, otherwise create it."""
    import webview
    if not api.base:
        print(f"[hud] {page}: no server address")
        return
    existing = getattr(api, attr, None)
    if existing is not None:
        try:
            existing.show(); existing.restore(); return
        except Exception:
            setattr(api, attr, None)
    try:
        win = webview.create_window(title, url=f"{api.base}/{page}#t={api.token}", width=width, height=height,
                                    min_size=(820, 560), background_color="#070b12")
    except Exception as e:
        print(f"[hud] {page} failed: {type(e).__name__}: {e}")
        return

    def _closed(*_):
        setattr(api, attr, None)
    win.events.closed += _closed
    setattr(api, attr, win)


# ---- small stores for the Living Presence Engine (data/) ----
def _data_file(name: str) -> Path:
    """Beside his saved position (data/ — tests point core.companion.default_position_path elsewhere)."""
    from core.companion import default_position_path
    return default_position_path().parent / name


def _load_preferred():
    """Where the user last put him by hand (a drag), or None."""
    try:
        raw = json.loads(_data_file("companion-preferred.json").read_text(encoding="utf-8"))
        return int(raw["x"]), int(raw["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_preferred(x: int, y: int) -> None:
    p = _data_file("companion-preferred.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")


def _clean_names(values, limit: int) -> list:
    out = []
    for v in values if isinstance(values, list) else []:
        if isinstance(v, str) and 0 < len(v) <= 64 and v not in out:
            out.append(v)
    return out[:limit]


def _load_skill_prefs() -> dict:
    """His skills menu: favourite and recently used skill names."""
    try:
        raw = json.loads(_data_file("companion-skills.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {"favorites": _clean_names(raw.get("favorites"), 40), "recent": _clean_names(raw.get("recent"), 12)}


def _save_skill_prefs(prefs) -> dict:
    prefs = prefs if isinstance(prefs, dict) else {}
    clean = {"favorites": _clean_names(prefs.get("favorites"), 40), "recent": _clean_names(prefs.get("recent"), 12)}
    p = _data_file("companion-skills.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean), encoding="utf-8")
    return clean


def _plain_http_error(code: int, detail):
    """Pure: the server's own explanation when it gave one; words instead of a bare HTTP status phrase otherwise."""
    import http
    try:
        phrase = http.HTTPStatus(int(code)).phrase
    except ValueError:
        phrase = ""
    if detail and detail != phrase:
        return detail
    if code in (401, 403):
        return "Jarvis didn't accept this window's key — restart Jarvis."
    if code in (404, 405):
        return "This Jarvis server doesn't offer that (it may need updating)."
    if code >= 500:
        return "Jarvis's server hit a problem with that — try again in a moment."
    return f"That didn't go through ({phrase or code})."


def _css_layout(lay: dict, dpr: float) -> dict:
    """core.presence.expand_layout's screen-pixel boxes, in the page's CSS pixels."""
    k = 1.0 / (dpr or 1.0)
    return {"side": lay["side"], "robot": [round(v * k, 1) for v in lay["robot"]],
            "panel": [round(v * k, 1) for v in lay["panel"]]}


def _open_dashboard(api: Api) -> None:
    import webview

    if not api.base:
        print("[hud] dashboard: no server address")
        return
    if api._dash is not None:
        try:
            api._dash.show()
            api._dash.restore()
            return
        except Exception:
            api._dash = None
    win = webview.create_window("J.A.R.V.I.S. · Command Center", url=f"{api.base}/dashboard.html#t={api.token}",
                                width=1440, height=900, min_size=(900, 600), background_color="#03070c")

    def closed(*_):
        api._dash = None
    win.events.closed += closed
    api._dash = win


def _create_panel_window(api: Api, title: str = "Jarvis"):
    """Create the original 400x660 frameless panel. Factored out so the
    companion mode can create it alongside a transparent companion window."""
    import webview
    screen = api._screen
    x = (screen.width - WIDTH - 16) if screen else None
    y = (screen.height - HEIGHT - 56) if screen else None
    return webview.create_window(
        title, url=str(HERE / "index.html"), js_api=api, width=WIDTH, height=HEIGHT, x=x, y=y,
        frameless=True, easy_drag=False, on_top=True, resizable=False,
        background_color="#03070c", focus=False, shadow=True)


CC_TITLE = "J.A.R.V.I.S. Command Center"
CC_DEFAULT = (1500, 850)
CC_MIN = (1100, 640)


def desktop_ui(base: str = "x", env=None) -> str:
    """Which main window: "command-center" (default) or "panel" (the old 400 px panel). JARVIS_DESKTOP_UI=panel goes
    back; without the local app server there is nothing to serve the Command Center, so the panel is used."""
    value = str((os.environ if env is None else env).get("JARVIS_DESKTOP_UI", "command-center")).strip().lower()
    return "panel" if (not base or value in ("panel", "classic", "old", "hud")) else "command-center"


def _screen_rects(screen_w: int = 0, screen_h: int = 0, screens=None) -> list:
    """(x, y, w, h) of every monitor, primary first; the old (width, height) pair is one monitor at the origin."""
    rects = []
    for sc in screens or []:
        try:
            rects.append((int(getattr(sc, "x", 0)), int(getattr(sc, "y", 0)), int(sc.width), int(sc.height)))
        except Exception:
            continue
    if not rects and screen_w and screen_h:
        rects.append((0, 0, int(screen_w), int(screen_h)))
    return rects


def load_cc_geometry(screen_w: int = 0, screen_h: int = 0, path=None, screens=None) -> dict:
    """Remembered size and position of the Command Center, kept on a monitor that exists now; otherwise it is centred on
    the primary monitor (or left to the window system when no monitor is known)."""
    rects = _screen_rects(screen_w, screen_h, screens)
    if rects:
        w, h = min(CC_DEFAULT[0], rects[0][2] - 40), min(CC_DEFAULT[1], rects[0][3] - 80)
    else:
        w, h = CC_DEFAULT
    out = {"width": max(CC_MIN[0], w), "height": max(CC_MIN[1], h), "x": None, "y": None}
    try:
        raw = json.loads((path or _data_file("command-center-window.json")).read_text(encoding="utf-8"))
        gw, gh, gx, gy = (int(raw[k]) for k in ("width", "height", "x", "y"))
    except Exception:
        return out
    if gw < CC_MIN[0] or gh < CC_MIN[1] or gw > 10000 or gh > 10000:
        return out
    out.update(width=gw, height=gh)
    if rects:
        on_a_monitor = any(sx - gw + 200 <= gx <= sx + sw - 200 and sy - 20 <= gy <= sy + sh - 120 for sx, sy, sw, sh in rects)
        if not on_a_monitor:                       # saved on a monitor that is gone: keep the size, centre on the primary
            px, py, pw, ph = rects[0]
            out.update(x=px + max(0, (pw - gw) // 2), y=py + max(0, (ph - gh) // 2))
            return out
    out.update(x=gx, y=gy)
    return out


def save_cc_geometry(geometry: dict, path=None) -> bool:
    try:
        g = {k: int(geometry[k]) for k in ("width", "height", "x", "y")}
        if g["width"] < CC_MIN[0] or g["height"] < CC_MIN[1] or g["x"] <= -30000 or g["y"] <= -30000:
            return False                           # a minimised window reports -32000: never remember that
        p = path or _data_file("command-center-window.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(g), encoding="utf-8")
        return True
    except Exception:
        return False


_CC_BOOT = """<!doctype html><meta charset="utf-8"><title>J.A.R.V.I.S.</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#04070D;color:#9fb6cc;font:15px Segoe UI,sans-serif;text-align:center">
<div><div id="m" style="letter-spacing:.3em;font-size:13px">STARTING J.A.R.V.I.S.</div><div id="s" style="margin-top:.8rem;opacity:.7"></div></div>
<script>
const URL_ = __URL__, T0 = Date.now();
async function up() { try { await fetch(URL_.split("#")[0], { mode: "no-cors", cache: "no-store" }); return true; } catch (e) { return false; } }
(async function loop() {
  if (await up()) { location.replace(URL_); return; }
  const secs = Math.round((Date.now() - T0) / 1000);
  document.getElementById("s").textContent = secs > 20 ? "Jarvis's server is not answering yet. Still trying (" + secs + " s)." : "Waiting for Jarvis's server.";
  setTimeout(loop, 1000);
})();
</script>"""


def cc_boot_html(url: str) -> str:
    """The Command Center's first page: a small loader that retries until Jarvis's local server answers, then opens the
    real page. The HUD process starts before the server is listening (main.py), and a window pointed straight at a dead
    port stays blank for good."""
    return _CC_BOOT.replace("__URL__", json.dumps(url).replace("</", "<" + chr(92) + "/"))


def cc_visibility(win, on: bool) -> None:
    """Tell the Command Center page whether its window is on screen: a hidden window pauses its polling (closing the window
    only hides it), and showing it again refreshes at once."""
    try:
        win.evaluate_js("window.jarvis && jarvis.handle(" + json.dumps({"type": "visibility", "value": bool(on)}) + ")")
    except Exception:
        pass


def _create_command_center_window(api: Api, title: str = CC_TITLE):
    """The main desktop window: the Command Center page from the local app server, a normal resizable window."""
    import webview
    screen = api._screen
    try:
        import webview as _wv
        screens = list(_wv.screens)
    except Exception:
        screens = []
    geo = load_cc_geometry(screen.width if screen else 0, screen.height if screen else 0, screens=screens)
    win = webview.create_window(title, html=cc_boot_html(f"{api.base}/command-center.html#t={api.token}"), js_api=api,
                                width=geo["width"], height=geo["height"], x=geo["x"], y=geo["y"], resizable=True,
                                min_size=CC_MIN, background_color="#04070D")
    state = dict(geo)

    def _remember(**kw):
        state.update(kw)
        for k in ("x", "y"):                       # a first-run window has no remembered position: ask the window
            if state.get(k) is None:
                state[k] = getattr(win, k, None)
        if state.get("x") is not None and state.get("y") is not None:
            save_cc_geometry(state)                # refuses a minimised window's sentinel position and tiny sizes

    def _closing(*_):
        if api._quitting:
            return True
        try:
            win.hide()                             # closing keeps Jarvis running: the tray's Show HUD brings it back
            cc_visibility(win, False)
        except Exception:
            return True
        return False
    try:
        win.events.resized += lambda w, h, *_: _remember(width=w, height=h)
        win.events.moved += lambda x, y, *_: _remember(x=x, y=y) if x > -30000 and y > -30000 else None
        win.events.closing += _closing
        win.events.restored += lambda *_: cc_visibility(win, True)
        win.events.minimized += lambda *_: cc_visibility(win, False)
    except Exception as e:
        print(f"[hud] command center events: {type(e).__name__}: {e}")
    return win


def open_panel(api: Api) -> None:
    """Legacy fallback used by Api.summon_panel when the primary panel is
    unavailable. In additive companion mode the panel is already live, so
    this is mostly a no-op path."""
    import webview
    if api._panel is not None:
        try:
            api._panel.show()
            api._panel.restore()
            return
        except Exception:
            api._panel = None
    win = _create_panel_window(api, title="Jarvis")

    def closed(*_):
        api._panel = None
    win.events.closed += closed
    api._panel = win


def _create_companion_window(api: Api, title: str = "Jarvis"):
    """Create the transparent companion window. Falls back to an opaque
    window if the platform refuses transparency. Returns (window, transparent)."""
    import webview
    from core.companion import (DEFAULT_POSITION, clamp_position, load_position, load_settings, window_for_character,
                                Monitor)

    settings = load_settings()
    cw, ch = window_for_character(settings["character_px"])
    screen = api._screen
    monitors = []
    try:
        for s in (webview.screens or []):
            monitors.append(Monitor(int(getattr(s, "x", 0) or 0),
                                    int(getattr(s, "y", 0) or 0),
                                    int(s.width), int(s.height)))
    except Exception:
        pass
    if not monitors and screen is not None:
        monitors = [Monitor(0, 0, int(screen.width), int(screen.height))]

    pos = load_position() or DEFAULT_POSITION
    x, y = clamp_position(pos[0], pos[1], cw, ch, monitors) if monitors else pos

    if not api.base:
        print("[hud] companion needs the local app server; skipping companion window")
        return None, False

    url = f"{api.base}/companion.html#t={api.token}"
    common = dict(url=url, js_api=api, width=cw, height=ch, x=x, y=y,
                  frameless=True, easy_drag=False, on_top=True, resizable=False, min_size=(50, 50),
                  focus=False, shadow=False)
    # pywebview 6.2.1 insists on a 6-char hex triplet for background_color
    # (an 8-char RGBA value like "#00000000" raises "not a valid hex triplet
    # color"). Transparency is driven by transparent=True + the renderer's
    # own alpha; background_color only sets what shows under the HTML, so
    # black is the safe choice for a transparent window — and the color-key
    # fallback (below) punches those black pixels through to the desktop.
    try:
        win = webview.create_window(title, background_color="#000000",
                                    transparent=True, **common)
        return win, True
    except TypeError as e:
        print(f"[hud] transparent window unsupported ({e}); using opaque fallback")
        win = webview.create_window(title, background_color="#000000", **common)
        return win, False
    except Exception as e:
        print(f"[hud] transparent window failed ({e}); using opaque fallback")
        win = webview.create_window(title, background_color="#000000", **common)
        return win, False


# ---------------------------------------------------------------- Win32 color-key
def _window_hwnd(window, title_hint: str = "") -> int:
    """Best-effort HWND lookup across pywebview versions.

    On pywebview 6.2.1 + EdgeChromium Windows backend, `window.native` returns
    None until the renderer is fully initialised and even then it may not be
    exposed. We fall back to FindWindowW by title_hint, which is reliable as
    long as the title is unique (the harness uses 'Jarvis (harness)')."""
    for attr in ("native_handle", "native"):
        try:
            v = getattr(window, attr, None)
            if callable(v):
                v = v()
            if v:
                return int(v)
        except Exception:
            continue
    try:
        return int(window.gui.get_native_handle(window))     # type: ignore[attr-defined]
    except Exception:
        pass
    if title_hint and os.name == "nt":
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, title_hint)
            if hwnd:
                return int(hwnd)
        except Exception:
            pass
    return 0


def _apply_colorkey_transparency(hwnd: int, color_bgr: int = 0x000000) -> bool:
    """Apply WS_EX_LAYERED + LWA_COLORKEY on `hwnd`. Pixels matching
    `color_bgr` (default = pure black) become fully see-through on composition.
    Safe no-op on non-Windows and on an invalid handle."""
    if not hwnd or os.name != "nt":
        return False
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    LWA_COLORKEY = 0x00000001
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return False
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    return bool(user32.SetLayeredWindowAttributes(hwnd, int(color_bgr), 0, LWA_COLORKEY))


# ---------------------------------------------------------------- Win32 smart positioning
# Real (physical-pixel) desktop geometry for the companion's "stay out of the way"
# behaviour. These feed core.companion.plan_companion_move. Everything here is one
# coordinate space — Win32 physical pixels — so moving via SetWindowPos is correct
# regardless of DPI (the per-monitor DPI → logical-pixel split only matters on a
# scaled display, noted as a limitation in the handoff; the owner's screens are 100%).
if os.name == "nt":
    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                    ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

    _MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double)


def _work_area_monitors():
    """Each monitor's WORK area (taskbar excluded) in physical pixels. Unlike the
    webview.Screen extents used for manual-drag clamping, this keeps the companion
    off the taskbar when it auto-places."""
    from core.companion import Monitor
    mons = []
    if os.name != "nt":
        return mons
    user32 = ctypes.windll.user32

    def _cb(hmon, hdc, lprc, lparam):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            w = mi.rcWork
            mons.append(Monitor(w.left, w.top, w.right - w.left, w.bottom - w.top))
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    except Exception:
        return []
    return mons


def _full_monitors():
    """Each monitor's FULL bounds (rcMonitor, taskbar INCLUDED) as (x, y, w, h) tuples.
    Used to tell a true full-screen app from a merely maximized window."""
    rects = []
    if os.name != "nt":
        return rects
    user32 = ctypes.windll.user32

    def _cb(hmon, hdc, lprc, lparam):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            m = mi.rcMonitor
            rects.append((m.left, m.top, m.right - m.left, m.bottom - m.top))
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    except Exception:
        return []
    return rects


def _foreground_rect(exclude_hwnds):
    """Rect (x, y, w, h) of the active window, or None when it is one of ours,
    minimized, or unavailable."""
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd in exclude_hwnds:
        return None
    if user32.IsIconic(hwnd):          # minimized → not on screen
        return None
    r = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return (r.left, r.top, w, h)


def _cursor_point():
    if os.name != "nt":
        return None
    pt = _POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return (pt.x, pt.y)
    return None


def _window_rect(hwnd):
    if os.name != "nt" or not hwnd:
        return None
    r = _RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def _move_window(hwnd, x: int, y: int) -> None:
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE — reposition only, never steal focus or raise.
    ctypes.windll.user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010)


# ---- Living Presence: cheap Win32 facts and window styles (no screen capture, no AI) ----
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TRANSPARENT = 0x00000020
_SHELL_DRAG_SOURCES = frozenset({"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"})
_proc_names: dict = {}


def _set_window_rect(hwnd, x: int, y: int, w: int, h: int) -> bool:
    """Move AND resize in one call (his menu opening/closing): SWP_NOZORDER | SWP_NOACTIVATE."""
    if os.name != "nt" or not hwnd:
        return False
    return bool(ctypes.windll.user32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h), 0x0004 | 0x0010))


def _set_ex_style(hwnd, add: int = 0, remove: int = 0) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, (style | add) & ~remove)
    return True


def _set_layered(hwnd, colorkey: int, alpha: int = 255) -> bool:
    """Keep the colour key (his see-through surroundings) and add overall opacity when below 255."""
    if os.name != "nt" or not hwnd:
        return False
    _set_ex_style(hwnd, add=0x00080000)                 # WS_EX_LAYERED
    flags = 0x1 | (0x2 if alpha < 255 else 0)            # LWA_COLORKEY (| LWA_ALPHA)
    return bool(ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, int(colorkey), int(alpha), flags))


def _force_foreground(hwnd, fg) -> bool:
    """SetForegroundWindow from a process the user didn't just interact with: attach to the foreground window's
    input thread, bring ours forward, detach. True when ours is the foreground window afterwards."""
    if os.name != "nt" or not hwnd:
        return False
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    ours = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)
    return user32.GetForegroundWindow() == hwnd


def _to_front(window, title_hint: str = "") -> bool:
    """He asked for this window ("open World Intelligence", "open the dashboard"): un-minimize it and put it in front of
    whatever he is using. pywebview's restore() only un-minimizes; a window behind his app stayed behind (2026-11-15)."""
    if os.name != "nt":
        return False
    try:
        hwnd = _window_hwnd(window, title_hint=title_hint)
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                    # SW_RESTORE
        return _force_foreground(hwnd, user32.GetForegroundWindow())
    except Exception:
        return False


def _mouse_down() -> bool:
    if os.name != "nt":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _mouse_used() -> bool:
    """A mouse button is down, or was pressed since the last call (the low bit: best-effort, but enough to tell a
    click or a release from a keystroke)."""
    if os.name != "nt":
        return False
    state = [ctypes.windll.user32.GetAsyncKeyState(vk) for vk in (0x01, 0x02, 0x04)]   # left, right, middle
    return any(s & 0x8001 for s in state)


def _class_name(hwnd) -> str:
    if os.name != "nt" or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _shell_is_foreground() -> bool:
    """A drag that starts in Explorer or on the desktop is most likely a file on its way somewhere."""
    if os.name != "nt":
        return False
    return _class_name(ctypes.windll.user32.GetForegroundWindow()) in _SHELL_DRAG_SOURCES


def _last_input_tick() -> int:
    if os.name != "nt":
        return 0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    return int(info.dwTime) if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)) else 0


def _window_info(hwnd) -> dict:
    """The foreground app's executable, class and title (process names cached by pid)."""
    if os.name != "nt" or not hwnd:
        return {}
    user32 = ctypes.windll.user32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = _proc_names.get(pid.value)
    if name is None:
        try:
            import psutil
            name = psutil.Process(pid.value).name().lower()
        except Exception:
            name = ""
        if len(_proc_names) > 256:
            _proc_names.clear()
        _proc_names[pid.value] = name
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(max(1, n + 1))
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return {"process": name, "title": buf.value, "class": _class_name(hwnd)}


if os.name == "nt":
    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong), ("hwndActive", ctypes.c_void_p),
                    ("hwndFocus", ctypes.c_void_p), ("hwndCapture", ctypes.c_void_p),
                    ("hwndMenuOwner", ctypes.c_void_p), ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p), ("rcCaret", _RECT)]


def _caret_point(hwnd):
    """Where the text caret is (screen px) in apps that expose it (most classic Win32 editors, many inputs);
    None otherwise. Placement keeps him away from the line being typed."""
    if os.name != "nt" or not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetGUIThreadInfo.argtypes = [ctypes.c_ulong, ctypes.POINTER(_GUITHREADINFO)]
        user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(_POINT)]
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        gi = _GUITHREADINFO()
        gi.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if not tid or not user32.GetGUIThreadInfo(tid, ctypes.byref(gi)) or not gi.hwndCaret:
            return None
        pt = _POINT(gi.rcCaret.left, gi.rcCaret.bottom)
        if not user32.ClientToScreen(gi.hwndCaret, ctypes.byref(pt)):
            return None
        return (pt.x, pt.y)
    except Exception:
        return None


def _enum_app_windows(exclude_hwnds):
    """[(hwnd, (x, y, w, h), title)] for normal alt-tab-able application windows —
    the ones a user 'opens' (Feature #151). Tooltips, menus, the taskbar and our
    own windows are filtered out by core.companion.is_app_window."""
    from core.companion import is_app_window
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER = -20, 0x00000080, 4
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, lparam):
        try:
            hwnd = hwnd or 0
            if not user32.IsWindowVisible(hwnd):
                return 1
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return 1
            tbuf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, tbuf, n + 1)
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            owner = user32.GetWindow(hwnd, GW_OWNER)
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cbuf, 256)
            if is_app_window(visible=True, title=tbuf.value, tool_window=bool(exstyle & WS_EX_TOOLWINDOW),
                             has_owner=bool(owner), hwnd=hwnd, classname=cbuf.value, exclude_hwnds=exclude_hwnds):
                r = _RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                    w, h = r.right - r.left, r.bottom - r.top
                    if w > 40 and h > 40 and not user32.IsIconic(hwnd):
                        out.append((hwnd, (r.left, r.top, w, h), tbuf.value))
        except Exception:
            pass
        return 1

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:
        return []
    return out


# The companion form's background is painted this colour and keyed out, so only the rendered robot shows.
# pywebview's "transparent" window leaves the WinForms form at the default light grey (#F0F0F0), which a black
# colour key never matched: the companion showed up as a grey box. A unique near-black can only collide with a
# render pixel of exactly (3, 2, 4), and anti-aliased edges blend toward a dark fringe, not a pale one.
KEY_RGB = (3, 2, 4)


def _make_form_see_through(window, hwnd: int) -> bool:
    """Paint the pywebview form KEY_RGB and key that colour out. False when the native form is unavailable
    (the window is then left as it is: a visible, opaque box is better than a crash)."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        import clr  # noqa: F401  (pythonnet is how pywebview talks to WinForms)
        from System import Action
        from System.Drawing import Color
        form = getattr(window, "native", None)
        if form is None:
            return False
        r, g, b = KEY_RGB
        def configure_background():
            form.BackColor = Color.FromArgb(r, g, b)
            # Set the actual WebView2 control, not the EdgeChrome wrapper. Restore
            # transparency after navigation as well as during initial construction.
            control = getattr(form, "webview", None)
            if control is not None and hasattr(control, "DefaultBackgroundColor"):
                control.DefaultBackgroundColor = Color.Transparent
        form.Invoke(Action(configure_background))
    except Exception as e:
        print(f"[hud] could not recolour the companion form: {type(e).__name__}: {e}")
        return False
    return _apply_colorkey_transparency(hwnd, (KEY_RGB[2] << 16) | (KEY_RGB[1] << 8) | KEY_RGB[0])


# ---- Hit mask: his silhouette takes the mouse (Living Presence) --------------------------------------
# Each cell is painted in his own colour there (the page samples it), so even a cell that peeks out for an instant
# while he moves looks like him; MASK_RGB is for a mask without colours. The open menu and his size slider sit on
# PANEL_RGB, the near-black navy of their own glass, which shows through it a few percent at most.
MASK_RGB = (88, 96, 110)
PANEL_RGB = (8, 11, 24)


def hitmask_grid(spec):
    """Pure: the page's mask ({cols, rows, w, h, dpr, bits: ["0110"...], colors: ["fff0a0..."...], rects: [[x, y, w,
    h]...]}, CSS px), validated -> {cols, rows, size: his box in window px, cells: {(col, row): (r, g, b)}, rects:
    extra boxes in window px (his size slider)}; None when it isn't a well-formed mask (mid-throw it has no cells)."""
    if not isinstance(spec, dict):
        return None
    try:
        cols, rows = int(spec["cols"]), int(spec["rows"])
        w, h = float(spec["w"]), float(spec["h"])
        k = float(spec.get("dpr") or 1.0)
        bits = spec["bits"]
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (0 < cols <= 64 and 0 < rows <= 64 and 0 < w <= 4000 and 0 < h <= 4000 and 0.5 <= k <= 4.0):
        return None
    if not isinstance(bits, list) or len(bits) != rows:
        return None
    colors = spec.get("colors")
    colors = colors if isinstance(colors, list) and len(colors) == rows else [None] * rows
    cells = {}
    for r, row in enumerate(bits):
        if not isinstance(row, str) or len(row) != cols:
            return None
        crow = colors[r] if isinstance(colors[r], str) and len(colors[r]) == 3 * cols else None
        for c, bit in enumerate(row):
            if bit != "1":
                continue
            rgb = MASK_RGB
            if crow:
                try:
                    v = int(crow[3 * c:3 * c + 3], 16)
                    rgb = ((v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17)
                except ValueError:
                    pass
            cells[(c, r)] = rgb
    import math
    rects = []
    extras = spec.get("rects")
    for extra in (extras[:4] if isinstance(extras, (list, tuple)) else []):
        try:
            x, y, rw, rh = (float(v) for v in extra)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(v) and abs(v) <= 4000 for v in (x, y, rw, rh)) and rw > 0 and rh > 0:
            rects.append((int(x * k), int(y * k), int(rw * k + 0.999), int(rh * k + 0.999)))
    return {"cols": cols, "rows": rows, "size": (int(round(w * k)), int(round(h * k))), "cells": cells, "rects": rects}


def hitmask_pixels(grid, key_rgb) -> bytes:
    """Pure: the cell grid as a cols x rows 32-bit BGRA image, top row first — the key colour where he isn't (it
    stays see-through and click-through), his colour where he is (never exactly the key, which would let it through)."""
    kb = bytes((key_rgb[2], key_rgb[1], key_rgb[0], 255))
    out = bytearray(kb * (grid["cols"] * grid["rows"]))
    for (c, r), (red, green, blue) in grid["cells"].items():
        if (red, green, blue) == tuple(key_rgb):
            red = min(255, red + 17)
        i = (r * grid["cols"] + c) * 4
        out[i:i + 4] = bytes((blue, green, red, 255))
    return bytes(out)


def hitmask_plan(spec, expanded, yielding: bool = False):
    """Pure: what takes the mouse — his cells (hitmask_grid) drawn over his box, which sits at the window's origin
    or, with his menu open, where core.presence.expand_layout put it; the menu's box once the page says it has faded
    in — or nothing at all while he yields. -> (grid or None, his box (x, y, w, h) or None, panel box or None)."""
    if yielding:
        return None, None, None
    grid = hitmask_grid(spec)
    spec = spec if isinstance(spec, dict) else {}
    box = None
    if grid:
        box = tuple(expanded["robot"]) if expanded else (0, 0, grid["size"][0], grid["size"][1])
    return grid, box, (expanded["panel"] if expanded and spec.get("panel") else None)


def _paint_hitmask(api) -> bool:
    """Paint the key colour everywhere and hitmask_plan's cells and boxes on top, as the WinForms form's background
    image: Windows hit-tests the colour-keyed window against exactly that, so those pixels take the mouse and the
    rest stays click-through. The cells go in as one tiny image scaled up without smoothing (one GDI+ call)."""
    import base64
    import time as _t
    window = api._companion
    form = getattr(window, "native", None) if window is not None else None
    if form is None or os.name != "nt":
        return False
    with api._hitmask_lock:                              # one painter at a time: the newest mask is the one shown
        grid, box, panel = hitmask_plan(api._hitmask, api._expanded, api._yield_until > _t.monotonic())
        key = api._colorkey
        key_rgb = (key & 0xFF, (key >> 8) & 0xFF, (key >> 16) & 0xFF)
        try:
            from System import Action, Convert
            from System.Drawing import Bitmap, Color, Graphics, Rectangle, SolidBrush
            from System.Drawing.Drawing2D import CompositingMode, GraphicsPath, InterpolationMode, PixelOffsetMode
            from System.Drawing.Imaging import ImageLockMode, PixelFormat
            from System.Runtime.InteropServices import Marshal
            from System.Windows.Forms import ImageLayout
            pixels = None
            if grid and grid["cells"]:                     # one interop call, not one per byte
                pixels = Convert.FromBase64String(base64.b64encode(hitmask_pixels(grid, key_rgb)).decode())

            def paint():
                w, h = int(form.ClientSize.Width), int(form.ClientSize.Height)
                if w <= 0 or h <= 0:
                    return
                bmp = Bitmap(w, h)
                g = Graphics.FromImage(bmp)
                g.Clear(Color.FromArgb(*key_rgb))
                if pixels is not None:
                    cols, rows = grid["cols"], grid["rows"]
                    cells = Bitmap(cols, rows, PixelFormat.Format32bppArgb)
                    data = cells.LockBits(Rectangle(0, 0, cols, rows), ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb)
                    Marshal.Copy(pixels, 0, data.Scan0, cols * rows * 4)
                    cells.UnlockBits(data)
                    g.CompositingMode = CompositingMode.SourceCopy           # exact colours: the key stays the key
                    g.InterpolationMode = InterpolationMode.NearestNeighbor
                    g.PixelOffsetMode = PixelOffsetMode.Half
                    g.DrawImage(cells, Rectangle(int(box[0]), int(box[1]), int(box[2]), int(box[3])))
                    cells.Dispose()
                    g.CompositingMode = CompositingMode.SourceOver
                # Body yields to folders/titlebars; deliberate menu/toolbar controls
                # paint AFTER that protection, like any normal foreground menu.
                from core.desktop_habitat import protected_local
                wr = _window_rect(api._companion_hwnd())
                if wr:
                    keybrush = SolidBrush(Color.FromArgb(*key_rgb))
                    for px, py, pw, ph in protected_local(api._protected, wr):
                        g.FillRectangle(keybrush, px, py, pw, ph)
                    keybrush.Dispose()
                brush = SolidBrush(Color.FromArgb(*PANEL_RGB))
                for (x, y, rw, rh) in (grid["rects"] if grid else []):
                    g.FillRectangle(brush, x, y, rw, rh)
                if panel:                                  # the menu panel, inset inside its rounded corners
                    px, py, pw, ph = panel
                    path, r, i = GraphicsPath(), 10, 3
                    x0, y0, x1, y1 = px + i, py + i, px + pw - i, py + ph - i
                    path.AddArc(x0, y0, 2 * r, 2 * r, 180, 90)
                    path.AddArc(x1 - 2 * r, y0, 2 * r, 2 * r, 270, 90)
                    path.AddArc(x1 - 2 * r, y1 - 2 * r, 2 * r, 2 * r, 0, 90)
                    path.AddArc(x0, y1 - 2 * r, 2 * r, 2 * r, 90, 90)
                    path.CloseFigure()
                    g.FillPath(brush, path)
                    path.Dispose()
                brush.Dispose()
                from core.desktop_habitat import protected_local
                wr = _window_rect(api._companion_hwnd())
                if wr:
                    keybrush = SolidBrush(Color.FromArgb(*key_rgb))
                    for px, py, pw, ph in protected_local(getattr(api,"_strict_protected",[]), wr):
                        g.FillRectangle(keybrush, px, py, pw, ph)
                    keybrush.Dispose()
                g.Dispose()
                old = form.BackgroundImage
                form.BackgroundImageLayout = getattr(ImageLayout, "None")   # never tiled after a resize
                form.BackgroundImage = bmp
                form.Invalidate()
                if old is not None:
                    old.Dispose()
            form.Invoke(Action(paint))
            return True
        except Exception as e:
            print(f"[hud] hit mask: {type(e).__name__}: {e}")
            return False


# ---- File drop (feature #152): a dropped file's FULL PATH -------------------------------------------
# The page's HTML5 drop event only exposes a file's name + bytes, so a dropped SHORTCUT could be caught but not
# opened by its real path. WebView2's supported route gives the path: the page hands the dropped File objects to
# the host (chrome.webview.postMessageWithAdditionalObjects) and each arrives as a CoreWebView2File with .Path —
# pywebview does exactly this for a DOM "drop" listener registered from Python (pywebviewFullPath).
# (The old route — WebView2 declining external drops so WM_DROPFILES reached the host window — never fired for a
# real drag: WebView2's own drop target sits on the child window under the cursor and refused the drop.)
def dropped_paths(event) -> list:
    """Pure: the full paths pywebview attached to a DOM drop event (files without one are skipped)."""
    files = (((event or {}).get("dataTransfer") or {}).get("files") or []) if isinstance(event, dict) else []
    return [f["pywebviewFullPath"] for f in files if isinstance(f, dict) and isinstance(f.get("pywebviewFullPath"), str)
            and f["pywebviewFullPath"]]


def _watch_webview_failures(window, api) -> bool:
    """Log every WebView2 process failure on his window (a GPU or renderer process that died and was restarted) and
    count it in presence_state, so a rendering problem seen later can be matched against it."""
    try:
        from System import Action
        form = getattr(window, "native", None)
        if form is None:
            return False
        done = {"ok": False}

        def hook():
            for ctl in form.Controls:
                core = getattr(ctl, "CoreWebView2", None) if "WebView2" in type(ctl).__name__ else None
                if core is None:
                    continue

                def failed(_sender, args):
                    import time as _t
                    kind = str(getattr(args, "ProcessFailedKind", "?"))
                    reason = str(getattr(args, "Reason", ""))
                    api._webview_failures = {"count": (api._webview_failures or {}).get("count", 0) + 1,
                                             "kind": kind, "reason": reason, "at": round(_t.time(), 1)}
                    print(f"[hud] companion WebView2 process failed: {kind} ({reason})")
                core.ProcessFailed += failed
                done["ok"] = True
        form.Invoke(Action(hook))
        return done["ok"]
    except Exception as e:
        print(f"[hud] webview failure watch: {type(e).__name__}: {e}")
        return False


def _wire_dom_drop(window, api) -> bool:
    """Listen for drops on the companion page from Python, so each dropped file arrives with its full path and goes
    to api._handle_native_drops (the page plays the catch itself and, told so, no longer uploads the bytes)."""
    try:
        from webview.dom import DOMEventHandler

        def on_drop(event):
            paths = dropped_paths(event)
            if paths:
                api._handle_native_drops(paths, notify_page=False)
        window.dom.document.on("drop", DOMEventHandler(on_drop, prevent_default=True))
        window.evaluate_js("window.__jarvisFullPathDrops = true")
        return True
    except Exception as e:
        print(f"[hud] file drop wiring failed: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------- run
def run(inbox, parent_pid: int | None = None, base: str = "", token: str = "",
        mode: str = "panel", title: str = "Jarvis", shared=None) -> None:
    """HUD process entry.

    mode="panel"     : one window (unchanged behavior).
    mode="companion" : panel + companion, created at boot. Panel is byte-for-
                       byte the existing 400x660 window; the companion is
                       additive, independently show/hide-able via
                       ``companion_show`` / ``companion_hide`` events.
    """
    import queue
    import sys
    import time

    import psutil
    import webview

    if sys.stdout is None or getattr(sys.stdout, "name", "") == "nul":
        log_path = _data_file("hud.log"); log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    print(f"\n===== HUD started {time.strftime('%Y-%m-%d %H:%M:%S')} mode={mode} (server {'on' if base else 'off'}) =====")

    api = Api(base, token, mode, shared=shared)
    screen = webview.screens[0] if webview.screens else None
    api._screen = screen

    ui = desktop_ui(base)
    companion_base_title = title
    if ui == "command-center":
        title = CC_TITLE if title == "Jarvis" else title
        panel_window = _create_command_center_window(api, title=title)
        api._cc = panel_window
    else:
        panel_window = _create_panel_window(api, title=title)
    print(f"[hud] main window: {ui}")
    api._window = panel_window
    api._mode = mode

    companion_window = None
    companion_title = ""
    transparent = False
    if mode == "companion":
        # Always suffix so Codex / tray / FindWindow can distinguish the
        # two windows unambiguously, even when the panel title was
        # customised (e.g. "Jarvis (harness)" from the smoke harness).
        companion_title = companion_base_title + " (companion)"
        companion_window, transparent = _create_companion_window(api, title=companion_title)
        api._companion = companion_window
        api._companion_title = companion_title

    panel_ready = threading.Event()
    panel_window.events.loaded += lambda *a: panel_ready.set()
    companion_ready = threading.Event()
    if companion_window is not None:
        companion_window.events.loaded += lambda *a: companion_ready.set()
    else:
        companion_ready.set()

    def _after_companion_loaded():
        if companion_window is None:
            return
        api._reset_page_state()
        hwnd = _window_hwnd(companion_window, title_hint=companion_title)
        api._companion_title = companion_title
        see_through = _make_form_see_through(companion_window, hwnd)
        ok = see_through or _apply_colorkey_transparency(hwnd, 0x000000)
        if not see_through:
            api._colorkey = 0x000000                  # the fallback keys black, not KEY_RGB
        print(f"[hud] companion color-key transparency hwnd={hwnd} applied={ok} native_transparent={transparent}")
        # WinForms shrinks a frameless window's client area by the frame it had at creation,
        # so ask for the derived size again now that the border is gone.
        api.set_character_size(api.load_companion_settings()["character_px"], persist=False)

    def _presence_loop():
        """The desktop half of the Living Presence Engine (core/presence.py). Twice a second it samples cheap Win32
        facts — the foreground window and its app, the cursor, the last input, the text caret — and:
          1. hides him over a full-screen game or video and restores him after (whatever the settings say);
          2. tells the page what the user is doing, only when that changes (he stays calm while they work);
          3. asks placement whether he is really in the way — at once when the active window changes, otherwise
             every 1.5 s — and if so dispatches a relocation the page animates (it holds the lease meanwhile).
        Any interaction lease from the page (hover, press, drag, throw, menu, game, skill) makes placement wait;
        the cursor merely coming near never moves him. Local Win32 only: no AI, no screen capture."""
        import time as _time

        from core import presence
        from core.companion import is_exclusive_fullscreen
        chwnd = _window_hwnd(companion_window, title_hint=companion_title)
        phwnd = _window_hwnd(panel_window, title_hint=title)
        user32 = ctypes.windll.user32 if os.name == "nt" else None
        seen = {"tick": 0, "cursor": None, "typing_until": 0.0, "activity": None, "fg": None, "eval": 0.0,
                "others": ((), 0.0), "pointed": 0.0}

        while True:
            _time.sleep(0.5)
            try:
                if companion_window is None or user32 is None:
                    continue
                if not chwnd:
                    chwnd = _window_hwnd(companion_window, title_hint=companion_title)
                    if not chwnd:
                        continue
                ours = {h for h in (chwnd, phwnd) if h}
                fg_hwnd = user32.GetForegroundWindow()
                fg = _foreground_rect(ours)
                fullscreen = is_exclusive_fullscreen(fg, _full_monitors())
                if api._user_hidden:                    # dismissed by the owner: already hidden and suspended
                    api._fs_hidden = False
                    continue
                if fullscreen and not api._fs_hidden:
                    api._dispatch({"type": "hide"}); api._fs_hidden = True
                    api._hitmask = None; _paint_hitmask(api)  # no invisible clickable patch over the game
                elif not fullscreen and api._fs_hidden:
                    api._dispatch({"type": "show"}); api._fs_hidden = False

                # What the user is doing. Input that came with no mouse movement and no mouse button was the
                # keyboard (a click comes right after the mouse moved to its target, so that doesn't count either).
                mono = _time.monotonic()
                tick, cur, buttons = _last_input_tick(), _cursor_point(), _mouse_used()
                if cur != seen["cursor"]:
                    seen["pointed"] = mono                   # the mouse itself moved (reaching for him counts)
                elif tick != seen["tick"] and not buttons and not seen.get("buttons") and mono - seen["pointed"] > 1.0:
                    seen["typing_until"] = mono + 3.0
                seen["tick"], seen["cursor"], seen["buttons"] = tick, cur, buttons
                typing = mono < seen["typing_until"] and fg_hwnd not in ours   # typing to HIM isn't work
                info = _window_info(fg_hwnd) if fg_hwnd and fg_hwnd not in ours else {}
                idle = api.idle_seconds()
                activity = presence.classify_activity(process=info.get("process", ""), title=info.get("title", ""),
                                                      typing=typing, idle_seconds=idle, fullscreen=fullscreen)
                if activity != seen["activity"]:
                    seen["activity"] = api._activity = activity
                    api._dispatch({"type": "activity", "value": activity})
                if api._fs_hidden:
                    continue                                 # nothing to place while hidden
                wins_now = [(h, r) for h, r, title in _enum_app_windows(ours)]
                api._shell_snapshot = api._shell_probe.snapshot(wins_now)
                api._protected = api._shell_snapshot["protected"]
                api._strict_protected = api._shell_snapshot.get("strict_protected",[])
                if api._habitat and not api._lease.blocks(mono) and api._expanded is None and _time.time() >= api._physics_until:
                    from core.desktop_habitat import anchor_for
                    from core.companion import Monitor
                    r = _window_rect(chwnd)
                    source = next((s for s in api._shell_snapshot["surfaces"] if s.id == api._habitat["surface_id"]), None)
                    updated = anchor_for(source, r[2:], [Monitor(*v) for v in _full_monitors()], fraction=api._habitat.get("fraction",.5), contact=api._surface_contact) if source and r else None
                    if updated:
                        settings=api.load_companion_settings()
                        far=not cur or ((cur[0]-(r[0]+r[2]/2))**2+(cur[1]-(r[1]+r[3]/2))**2)**.5>presence.REACH_RADIUS
                        if settings.get("auto_place",True) and activity in ("idle","away","other") and far and mono-api._habitat_visit_at>90:
                            api._habitat_trip+=1
                            visit="favorite" if api._habitat_trip%2==0 else ("folder" if api._habitat_trip%3==0 else "window")
                            api._habitat_visit_at=mono
                            if api.companion_habitat(visit).get("ok"): continue
                        if (updated["x"], updated["y"]) != r[:2]:
                            _move_window(chwnd, updated["x"], updated["y"])
                        if updated["kind"] == "folder":
                            from core.desktop_habitat import protected_local
                            clips=protected_local([updated["rect"]], (updated["x"],updated["y"],*r[2:]))
                            updated["clip"]=list(clips[0]) if clips else [0,0,0,0]
                        api._habitat = updated
                        api._dispatch({"type": "surface", "value": updated})
                        _paint_hitmask(api)
                        continue
                    api._habitat = None
                    api._surface_contact = None
                    api._dispatch({"type": "surface", "value": None})
                    if not api.companion_habitat("favorite").get("ok"): api.companion_home()
                    continue


                # Where he rests.
                if os.getenv("JARVIS_COMPANION_AUTOPLACE", "1").strip().lower() in ("0", "false", "no", "off"):
                    continue
                settings = api.load_companion_settings()
                if not settings.get("auto_place", True):
                    continue
                if api._scene_mode or api._lease.blocks(mono) or _time.time() < api._physics_until or api._expanded is not None:
                    continue                                 # an interaction owns him right now
                if not user32.IsWindowVisible(chwnd):
                    continue
                changed = fg_hwnd != seen["fg"]
                seen["fg"] = fg_hwnd
                if not changed and mono - seen["eval"] < 1.5:
                    continue
                seen["eval"] = mono
                rect, monitors = _window_rect(chwnd), _work_area_monitors()
                if not rect or not monitors:
                    continue
                if mono - seen["others"][1] > 3.0:          # other windows change slowly: refresh every 3 s
                    seen["others"] = (tuple(r for (h, r, _t) in _enum_app_windows(ours) if h != fg_hwnd)[:24], mono)
                caret = _caret_point(fg_hwnd) if typing and fg_hwnd not in ours else None
                scene = presence.Scene(tuple(monitors), (rect[2], rect[3]), current=(rect[0], rect[1]), foreground=fg,
                                       others=seen["others"][0], caret=caret, cursor=cur, user_active=idle < 5,
                                       preferred=api._preferred, pointing=mono - seen["pointed"] < 2.0)
                plan = presence.plan_placement(scene, now=_time.time(), last_move=api._last_auto_move,
                                               manual_until=api._manual_until, docked=api._docked,
                                               reduced_motion=bool(settings.get("reduced_motion")))
                api._last_plan = plan.as_dict()
                if plan.target is None and plan.reason == "clear" and activity in ("idle", "away", "other"):
                    if mono - api._habitat_visit_at > 75 and _time.time() >= api._manual_until and not scene.pointing:
                        api._habitat_visit_at = mono
                        api.companion_habitat("favorite")
                        continue
                if plan.target is not None:
                    api._last_auto_move = _time.time()
                    api._last_move_plan = {**api._last_plan, "at": round(api._last_auto_move, 1), "from": list(rect[:2]),
                                           "cursor": list(cur) if cur else None, "pointing": scene.pointing,
                                           "foreground": list(fg) if fg else None}
                    api._dispatch({"type": "relocate", "x": int(plan.target[0]), "y": int(plan.target[1]),
                                   "style": plan.style, "dock": plan.dock})
            except Exception as e:
                print(f"[hud] presence: {type(e).__name__}: {e}")
                _time.sleep(2.0)

    def _app_react_loop():
        """Notice a newly launched app window and tell the companion to run over and
        kick it (Feature #151). Local Win32 only, rate-limited, no AI. He only reacts
        to NEW windows (diff against a seeded set), never to focus changes."""
        import json as _json
        import random
        import time as _time

        from core.companion import is_app_window  # noqa: F401  (imported for side-effect parity / clarity)
        user32 = ctypes.windll.user32 if os.name == "nt" else None
        chwnd = _window_hwnd(companion_window, title_hint=companion_title)
        phwnd = _window_hwnd(panel_window, title_hint=title)
        known = None
        last_react = 0.0
        while True:
            _time.sleep(1.2)
            try:
                if companion_window is None or user32 is None:
                    continue
                if os.getenv("JARVIS_COMPANION_APPREACT", "1").strip().lower() in ("0", "false", "no", "off"):
                    continue
                if not api.load_companion_settings().get("app_reactions", True):
                    continue
                if not chwnd:
                    chwnd = _window_hwnd(companion_window, title_hint=companion_title)
                exclude = {h for h in (chwnd, phwnd) if h}
                wins = _enum_app_windows(exclude)
                ids = {h for (h, _r, _t) in wins}
                if known is None:                 # seed: don't react to everything already open at boot
                    known = ids
                    continue
                fresh = [(h, r, ti) for (h, r, ti) in wins if h not in known]
                known = ids
                now = _time.time()
                if not fresh or now - last_react < 8.0:
                    continue
                if api._scene_mode or api._lease.blocks(_time.monotonic()) or api._expanded is not None:
                    continue                      # the user is busy with him (held, menu, game...): no kick now
                if api._fs_hidden or not user32.IsWindowVisible(chwnd):
                    continue
                h, rect, _ti = max(fresh, key=lambda it: it[1][2] * it[1][3])   # the biggest new window
                from core.companion import is_exclusive_fullscreen
                if is_exclusive_fullscreen(rect, _full_monitors()): continue
                tx, ty = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
                big = rect[2] * rect[3] > 0.35 * 1920 * 1080
                comedy = (big and random.random() < 0.5) or (random.random() < 0.15)
                last_react = now
                payload = _json.dumps({"type": "appreact", "target": [tx, ty], "comedy": bool(comedy)})
                try:
                    companion_window.evaluate_js(f"window.companion && companion.handle({payload})")
                except Exception as e:
                    print(f"[hud] appreact dispatch: {type(e).__name__}: {e}")
            except Exception as e:
                print(f"[hud] appreact: {type(e).__name__}: {e}")
                _time.sleep(2.0)

    def pump() -> None:
        panel_ready.wait()
        companion_ready.wait()
        _after_companion_loaded()
        if companion_window is not None:
            try:
                companion_window.evaluate_js(
                    f"window.companion && companion.init({json.dumps({'transparent': transparent})})")
            except Exception as e:
                print(f"[hud] companion init: {type(e).__name__}: {e}")
            try:                                         # he waits off screen until called ("come here"); remembered
                from core.hud import companion_visible
                api._user_hidden = not companion_visible()
            except Exception:
                api._user_hidden = False
            if api._user_hidden:
                try:
                    companion_window.hide()
                    companion_window.evaluate_js("window.companion && companion.handle({\"type\":\"hide\"})")
                except Exception as e:
                    print(f"[hud] companion start hidden: {type(e).__name__}: {e}")
            print(f"[hud] companion on screen: {not api._user_hidden}")
            threading.Thread(target=_presence_loop, daemon=True, name="companion-presence").start()
            threading.Thread(target=_app_react_loop, daemon=True, name="companion-appreact").start()

            # File drop (feature #152): each file dropped on him arrives with its FULL path, so a dropped
            # shortcut opens by its real path (WebView2's own route; see _wire_dom_drop).
            print(f"[hud] file drop (full paths): wired={_wire_dom_drop(companion_window, api)}")
            print(f"[hud] WebView2 failure watch: {_watch_webview_failures(companion_window, api)}")

        def _to_panel(msg):
            payload = json.dumps(msg)
            for win in (panel_window, api._panel if api._cc is not None else None):
                if win is None:
                    continue
                try:
                    win.evaluate_js(f"window.jarvis && jarvis.handle({payload})")
                except Exception as e:
                    print(f"[hud] panel dispatch {msg.get('type')}: {type(e).__name__}: {e}")

        def _quit():
            api._quitting = True
            for win in (api._panel if api._cc is not None else None, panel_window, companion_window):
                if win is None:
                    continue
                try: win.destroy()
                except Exception: pass

        def _to_companion(msg):
            if companion_window is None:
                return
            payload = json.dumps(msg)
            try:
                companion_window.evaluate_js(f"window.companion && companion.handle({payload})")
            except Exception as e:
                print(f"[hud] companion dispatch {msg.get('type')}: {type(e).__name__}: {e}")

        def _companion_scheduler(action):
            if companion_window is None:
                return
            try:
                companion_window.evaluate_js(
                    f"window.companion && companion.handle({json.dumps({"type": "hide" if action == "suspend" else "show"})})")
            except Exception:
                pass

        while True:
            try:
                msg = inbox.get(timeout=3)
            except queue.Empty:
                if parent_pid and not psutil.pid_exists(parent_pid):
                    _quit()
                    return
                continue
            if msg is None or msg.get("type") == "quit":
                _quit()
                return

            t = msg.get("type")

            # Visibility: hide/show stays panel-only for back-compat; the
            # companion has its own companion_show / companion_hide events.
            if t == "show":
                panel_window.show()
                if api._cc is not None:
                    try: panel_window.restore()
                    except Exception: pass
                    cc_visibility(panel_window, True)
                continue
            if t == "hide":
                panel_window.hide()
                if api._cc is not None:
                    cc_visibility(panel_window, False)
                continue
            if t == "companion_show":
                if companion_window is None:
                    continue
                api._user_hidden = False
                companion_window.show()
                _companion_scheduler("resume")
                # Re-apply the colour key Windows may clear on hide — the SAME key the form is painted with
                # (re-keying black here used to leave his see-through surroundings as a dark box).
                _set_layered(_window_hwnd(companion_window, title_hint=companion_title), api._colorkey, api._alpha)
                continue
            if t == "habitat":
                api.companion_habitat(msg.get("action", "favorite"))
                continue
            if t == "menu":
                api._end_yield(force=True)            # the menu hotkey also brings back a yielding companion
            if t == "companion_hide":
                if companion_window is None:
                    continue
                api._user_hidden = True
                companion_window.hide()
                _companion_scheduler("suspend")
                continue
            if t == "wardrobe":
                try: api.open_wardrobe()
                except Exception as e: print(f"[hud] wardrobe: {e}")
                continue
            if t == "front":
                try: panel_window.show(); panel_window.restore()
                except Exception: pass
                _to_front(panel_window, title)
                if api._cc is not None:
                    cc_visibility(panel_window, True)
                continue
            if t == "dashboard":
                if api._cc is not None:                # the Command Center IS the dashboard now: bring it forward
                    try: panel_window.show(); panel_window.restore()
                    except Exception: pass
                    _to_front(panel_window, title)
                    cc_visibility(panel_window, True)
                    _to_panel({"type": "mode", "value": "dashboard"})
                    continue
                open_dashboard(api)
                continue

            # Content routing — additive.
            if t in COMPANION_LOCAL:
                _to_companion(msg)
                if t == "doing" and api._cc is not None:
                    _to_panel(msg)                 # the Command Center shows the task row
                continue
            if t in PANEL_LOCAL:
                if t == "mode":
                    panel_window.show()
                    if api._cc is not None:                # workout / play / focus are views of the Command Center
                        try: panel_window.restore()
                        except Exception: pass
                        _to_front(panel_window, title)
                        cc_visibility(panel_window, True)
                _to_panel(msg)
                continue
            if t in BOTH_SURFACES:
                _to_panel(msg)
                _to_companion(msg)
                continue
            # Default: back-compat dispatch to the panel.
            _to_panel(msg)

    threading.Thread(target=pump, daemon=True).start()
    webview.settings["ALLOW_DOWNLOADS"] = True    # the House page's "Download" saves a recorded clip (WebView2's own save prompt)
    webview.start(gui="edgechromium")
