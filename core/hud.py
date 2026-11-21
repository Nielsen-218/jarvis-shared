"""Talk to the HUD window from anywhere in Jarvis. Every function is a no-op
when the HUD isn't running (text mode, tests), so callers never need to check.

Two UI modes share this transport:
  - "panel"     — the original 400x660 frameless on-top panel (hud/index.html).
  - "companion" — the transparent 3D concept-robot desktop character served from
                  the app server at /companion.html. See core/companion.py for
                  the event contract and app/companion.html for the renderer.
Switching happens at process start; a running HUD does not hot-swap modes.

IPC is bounded (``IPC_MAX``) so a stuck child cannot grow memory without
limit. Level traffic (hud.level) is noisy by nature; if the queue is full
when a level update arrives, it is dropped on the floor — a fresher level
will arrive on the next tick. Non-level messages (state transitions, line,
mode, dashboard, quit...) are important: when the queue is full they wait
briefly rather than being dropped, so a storm of level packets cannot
starve a state transition or a quit signal.
"""
import base64
import io
import multiprocessing as mp
import queue as _queue_mod
import json
import re
from pathlib import Path

from core import companion as _companion
from core import diag

IPC_MAX = 256               # outstanding events; main loop sends state at ~14 Hz
IMPORTANT_PUT_TIMEOUT = 0.1 # seconds to wait when the queue is full
LEVEL_TYPES = frozenset({"level"})

_queue = None
_process = None
_mode = "panel"
_last_level = -1.0
_dropped_levels = 0         # dropped under pressure — surfaced by stats() for diagnostics
_media_listeners = []  # e.g. the phone bot, so try-on images reach the phone too

# Multi-source amplitude arbitration. Both the main TTS loop (main.py:188)
# and the panel's quip bridge (hud/index.html -> Api.companion_level) push
# levels; without arbitration main's steady 0 ticks would overwrite a quip
# in progress. Each source keeps its last-nonzero timestamp here; a quiet
# source doesn't win over an actively speaking one until it goes stale.
_PANEL_QUIP_GRACE_MS = 500          # how long a panel-quip source stays "active" after its last non-zero sample
_source_last_nonzero_ms: dict[str, int] = {}
_active_source: str = "main"

# A short record of what Jarvis is doing and saying, for other screens (the TV dashboard).
import collections
import time as _time

_feed = collections.deque(maxlen=12)
_state = {"value": "sleeping", "since": _time.time()}
_voice_trace = collections.deque(maxlen=128)


def voice_trace() -> list:
    """Bounded monotonic lifecycle timestamps; never records speech content."""
    return list(_voice_trace)


def feed() -> dict:
    return {"state": _state["value"], "lines": list(_feed)}


def add_media_listener(fn) -> None:
    _media_listeners.append(fn)


def start(mode: str = "panel") -> None:
    """Boot the HUD process. mode='panel' (default) opens the original window;
    mode='companion' opens the transparent desktop character. Any other value
    falls back to 'panel' so a bad caller cannot leave the user without a HUD."""
    global _queue, _process, _mode, _dropped_levels
    if _process is not None:
        return
    from hud.app import run

    _mode = mode if mode in ("panel", "companion") else "panel"
    _queue = mp.Queue(maxsize=IPC_MAX)
    _dropped_levels = 0
    import os

    base = token = ""
    try:  # the companion reaches the local app server (workouts, Jarvis's voice, the dashboard) with this PC's token
        from core.appserver import PORT, pc_browser_token
        base, token = f"http://127.0.0.1:{PORT}", pc_browser_token("owner")
    except Exception as e:
        print(f"[hud] companion features offline: {e}")
    _process = mp.Process(target=run, args=(_queue, os.getpid(), base, token, _mode),
                          daemon=True, name="jarvis-hud")
    _process.start()


def mode_name() -> str:
    """Return the mode this process was started in."""
    return _mode


def running() -> bool:
    return _process is not None and _process.is_alive()


def _send(msg: dict) -> None:
    """Validate and ship an event to the window.

    Unknown/malformed events are dropped silently. Backpressure policy:
      * level: dropped on a full queue (a fresher value arrives shortly).
      * everything else: wait up to IMPORTANT_PUT_TIMEOUT so state/quit do
        not get starved by a storm of level packets.
    """
    global _dropped_levels
    if _queue is None or not running():
        return
    ev = _companion.validate_event(msg)
    if ev is None:
        return
    try:
        _queue.put_nowait(ev)
        return
    except _queue_mod.Full:
        pass
    except Exception:
        return
    if ev["type"] in LEVEL_TYPES:
        _dropped_levels += 1
        return
    try:
        _queue.put(ev, timeout=IMPORTANT_PUT_TIMEOUT)
    except (_queue_mod.Full, Exception):
        pass


def stats() -> dict:
    """Lightweight introspection for tests and diagnostics."""
    size = -1
    if _queue is not None:
        try:
            size = _queue.qsize()
        except (NotImplementedError, OSError):
            size = -1
    return {"mode": _mode, "running": running(), "queue_size": size,
            "queue_max": IPC_MAX, "dropped_levels": _dropped_levels}


def state(value: str) -> None:
    """idle | sleeping | listening | thinking | speaking | working | muted | error"""
    _voice_trace.append({"event": "state", "value": value, "at_ms": int(_time.monotonic() * 1000)})
    _state.update(value=value, since=_time.time())
    _send({"type": "state", "value": value})


def level(value: float, source: str = "main") -> None:
    """TTS output amplitude in [0, 1], tagged with its producer.

    Two producers call this: the main voice-loop (`main.py:188`, source
    `"main"`, ~14 Hz, zero when silent) and the panel-quip bridge
    (`hud.app.Api.companion_level`, source `"panel-quip"`, ~25 Hz, only
    while an Audio element in the panel is playing). Without arbitration
    the main loop's steady 0 ticks would overwrite a quip's non-zero
    samples and the mouth would never open for panel-played audio.

    Arbitration rule: a source stays "active" for ``_PANEL_QUIP_GRACE_MS``
    after its last non-zero sample. While a non-main source is active,
    main-source zero ticks are suppressed; non-zero samples from any
    source are always honored (so a real interrupt is never muted).
    """
    global _last_level, _active_source
    if source == "main":
        _poll_doing()
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    if v != v:                                  # NaN
        return
    v = max(0.0, min(1.0, v))
    now_ms = int(_time.monotonic() * 1000)

    if v > 0.02:
        _source_last_nonzero_ms[source] = now_ms
        _active_source = source
    else:
        # A quiet main tick cannot pre-empt an active non-main source
        # inside the grace window.
        for other, when in list(_source_last_nonzero_ms.items()):
            if other == source:
                continue
            if other != "main" and (now_ms - when) < _PANEL_QUIP_GRACE_MS:
                return

    if abs(v - _last_level) > 0.02:
        _last_level = v
        _send({"type": "level", "value": round(v, 3), "source": source})


_last_doing = ""
_doing_poll_at = 0.0
DOING_POLL_S = 0.25            # the voice loop ticks ~14 times a second; the chip needs a look four times a second


def doing(text: str) -> None:
    """What a tool is busy with right now ("opening Spotify"), for the chip on the companion's overlay card; ""
    when it finished. Sent only when it changes."""
    global _last_doing
    try:
        text = " ".join(str(text or "").split())[:120]
        if text == _last_doing or _queue is None or not running():
            return
        _queue.put_nowait({"type": "doing", "value": text})   # never waits: a full queue just means "try again"
        _last_doing = text                                     # remembered only once it was really sent
    except Exception:
        pass                                                   # this runs inside the voice loop: nothing may escape


def _poll_doing() -> None:
    """core.progress already knows what each tool is doing (brain and gateway report there). The voice loop calls
    level() several times a second, so that is where the overlay's chip is kept current: no new call sites."""
    global _doing_poll_at
    try:
        t = _time.monotonic()
        if t - _doing_poll_at < DOING_POLL_S:
            return
        _doing_poll_at = t
        from core import progress
        now = progress._now
        doing(now.get("step") or now.get("what") or "")
    except Exception:
        pass


def line(who: str, text: str) -> None:
    """who: 'you', 'jarvis' or 'sys'"""
    if who == "jarvis" and _feed and _feed[-1]["who"] == "jarvis" and _time.time() - _feed[-1]["t"] < 20:
        _feed[-1]["text"] += " " + text  # one reply = one line, like the HUD
        _feed[-1]["t"] = _time.time()
    else:
        _feed.append({"who": who, "text": text, "t": _time.time()})
    _send({"type": "line", "who": who, "text": text})


def badges(**values) -> None:
    """e.g. badges(eyes=True, lang='KREYOL', muted=False)"""
    _send({"type": "badges", "value": values})


def media(path: str | Path | None = None, caption: str = "", link: str | None = None, image=None) -> None:
    """Show an image (a file path, or a PIL image) in the HUD's media panel."""
    from PIL import Image

    for fn in _media_listeners:
        try:
            fn(path, caption)
        except Exception as error:
            diag.swallowed("media listener", error)

    try:
        img = image if image is not None else Image.open(path)
        img = img.convert("RGB")
        img.thumbnail((720, 720))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
    except Exception:
        return
    src = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    _send({"type": "media", "src": src, "caption": caption, "path": str(path) if path else "",
           "link": link or ""})


def show() -> None:
    _send({"type": "show"})


def front() -> bool:
    """Un-minimize the Hub window and bring it in front of whatever he is using, without changing its page."""
    _send({"type": "front"})
    return running()


def dashboard() -> bool:
    """Open (or bring forward) the Command Center window. False when the HUD isn't running."""
    _send({"type": "dashboard"})
    _opened("the Command Center")
    return running()


# What of Jarvis's own he opened last, so "bring it forward" / "I don't see it" right after means that window (his real
# lines 2026-11-14/15: "Open Dashboard." -> "Bring it forward." -> "I don't see it."; "Open World Intelligence." -> "Bring
# it to the front." -> "Should I bring Pythonw to the front?").
_LAST_OWN: dict = {"what": "", "at": 0.0}
OWN_RECENT_S = 180.0


def _opened(what: str) -> None:
    import time
    _LAST_OWN.update(what=what, at=time.monotonic())


def recent_own() -> str:
    import time
    return _LAST_OWN["what"] if _LAST_OWN["what"] and time.monotonic() - _LAST_OWN["at"] <= OWN_RECENT_S else ""


HUB_TITLES = ("J.A.R.V.I.S. Command Center",)


def in_front(timeout: float = 2.0) -> bool | None:
    """Is the Hub window (Command Center / World Intelligence) the window in front, within `timeout`? None: cannot tell
    (not Windows). Jarvis said "The Command Center is up on your screen" while it sat behind his app (2026-11-14)."""
    import os
    import time
    if os.name != "nt":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        end = time.monotonic() + timeout
        while True:
            hwnd = user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if any(t in buf.value for t in HUB_TITLES):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.15)
    except Exception:  # noqa: BLE001
        return None


def wardrobe() -> bool:
    """Open (or bring forward) the Wardrobe window, so a styled outfit is seen as well as heard."""
    _send({"type": "wardrobe"})
    return running()


def companion_action(action: str) -> bool:
    """Route physical robot commands through the EXISTING HUD event queue."""
    from core.companion import ROBOT_ACTIONS
    if action not in ROBOT_ACTIONS or not running() or _mode != "companion": return False
    _send({"type":"companion_action","action":action})
    return True


# ---- the companion comes when called and leaves when told (owner 2026-11-12) -------------------------------------------
_CHANNEL_NOTE = re.compile(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", re.I)
_ADDRESS = r"(?:jarvis|companion|buddy|robot)"
_LEAD = re.compile(rf"^(?:(?:hey|ok|okay|yo)\s+)?{_ADDRESS}\s*[,.!:]?\s+|^(?:please|now|alright|all right|okay|ok)\s*,?\s+", re.I)
_TAIL = re.compile(rf"\s*,?\s+(?:{_ADDRESS}|please|now|sir)$", re.I)
_SCREEN = r"(?:on|to|onto) (?:the |my )?(?:screen|desktop)"
_CALL_SHOW = re.compile(
    rf"(?:come here|come out|come back|come (?:back )?{_SCREEN}|show yourself|companion on|"
    rf"(?:turn|switch) (?:the |my )?companion (?:back )?on|(?:turn|switch) on (?:the |my )?companion|"
    rf"i need you {_SCREEN}|i need you here|(?:show|bring back|bring out|bring up|call|summon) (?:the |my )?companion)", re.I)
_CALL_HIDE = re.compile(
    r"(?:go away|leave|leave me|leave (?:the |my )?(?:screen|desktop)|hide|hide yourself|dismiss|dismissed|"
    r"you(?:'re| are) dismissed|that(?:'ll| will) be all,? (?:go|you (?:can|may) go)|companion off|"
    r"(?:turn|switch) (?:the |my )?companion off|(?:turn|switch) off (?:the |my )?companion|"
    r"(?:hide|dismiss) (?:the |my )?companion|get off (?:the |my )?(?:screen|desktop))", re.I)


_ONE_WORD = frozenset({"leave", "hide", "dismiss", "dismissed", "leave me"})


def companion_call(text: str, answering: bool = True) -> str | None:
    """"show" / "hide" when the WHOLE line calls or dismisses the desktop companion, else None. Whole-line matches only,
    so ordinary sentences ("go away for the weekend", "hide the window", "leave it") are never taken. `answering`: a bare
    "leave" / "hide" / "dismiss" may be his answer to something Jarvis asked or to an open lesson, not a dismissal. It is
    taken as one only when the caller KNOWS nothing is being answered (answering=False); a caller with no history to look
    at (the phone-relayed PC path) leaves the default, so only unambiguous phrases ("go away", "companion off") dismiss."""
    said = _CHANNEL_NOTE.sub("", str(text or ""))
    said = " ".join(said.replace("\u2019", "'").split()).strip(" .!?,").lower()
    for _ in range(4):                                   # "hey jarvis, please come here, sir"
        if not said or len(said) > 60:
            return None
        if _CALL_SHOW.fullmatch(said):
            return "show"
        if _CALL_HIDE.fullmatch(said):
            return None if (answering and said in _ONE_WORD) else "hide"
        trimmed = _TAIL.sub("", _LEAD.sub("", said)).strip(" .!?,")
        if trimmed == said:
            return None
        said = trimmed
    return None


def _visible_path() -> Path:
    return _companion.default_position_path().parent / "companion-visible.json"


def companion_visible() -> bool:
    """Whether he is on screen. Hidden until called; remembered across restarts."""
    try:
        return bool(json.loads(_visible_path().read_text(encoding="utf-8")).get("visible"))
    except Exception:
        return False


def set_companion_visible(on: bool) -> bool:
    """Show or dismiss the desktop companion and remember it. False, and nothing remembered, when he isn't running on
    this computer (a flag written for a companion that never moved would be wrong on the next start)."""
    on = bool(on)
    if not running() or _mode != "companion":
        return False
    try:
        p = _visible_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"visible": on}), encoding="utf-8")
    except OSError:
        return False
    _send({"type": "companion_show" if on else "companion_hide"})
    _send({"type": "companion", "visible": on})          # the Command Center's button follows
    return True


def companion_command(text: str, answering: bool = True) -> str | None:
    """A spoken or typed "come here" / "go away": done in code, never by a model. None when the line isn't one. A bare
    one-word dismissal needs answering=False: the caller has checked that no question or lesson is waiting for it."""
    want = companion_call(text, answering=answering)
    if want is None:
        return None
    if not set_companion_visible(want == "show"):
        return "The desktop companion isn't running on this computer, sir."
    return "Right here, sir." if want == "show" else "As you wish, sir. Say \"come here\" when you want me."


def world(preset: str = "standard") -> bool:
    """Open World Intelligence in the Hub window with a preset (core/worldintel.PRESETS). False when no HUD."""
    _send({"type": "mode", "value": "world", "preset": str(preset)[:20]})
    _opened("World Intelligence")
    return running()


def house(cam: str = "") -> bool:
    """Open the house camera page in the Hub window on one camera (core/homecam.py). False when no HUD."""
    _send({"type": "mode", "value": "house", "cam": str(cam)[:32]})
    _opened("House cameras")
    return running()


def mode(value: str) -> bool:
    """Switch the companion: 'workout', 'play', 'joke', 'chat'. False when the HUD isn't running."""
    _send({"type": "mode", "value": value})
    return running()


def companion_menu(action: str = "toggle", view: str | None = None) -> bool:
    """Open / close / toggle the desktop companion's own menu (the Ctrl+Alt+K hotkey). False when not running."""
    msg = {"type": "menu", "action": action}
    if view:
        msg["view"] = view
    _send(msg)
    return running()


def settings(**values) -> None:
    """Push live settings (quality/scale/fps/reduced_motion) to the renderer.
    Values are clamped by companion.safe_settings() before being applied."""
    _send({"type": "settings", "value": values})


def hide() -> None:
    _send({"type": "hide"})


def stop() -> None:
    global _process
    _send({"type": "quit"})
    if _process is not None:
        _process.join(timeout=2)
        _process = None


# Jarvis's own screens, asked for in words. These must never reach the PC-app launcher ("open the dashboard on
# the PC" used to try to launch a Windows program called "dashboard" and fail).
_VERB = r"(?:open|show|launch|start|bring up|pull up|put up|display|load|begin|run|do|coach me through|let'?s do)"
_OWN = (
    ("dashboard", re.compile(rf"\b{_VERB}\b.*\b(?:dashboard|command cent(?:er|re))\b", re.I)),
    ("workout", re.compile(rf"\b{_VERB}\b.*\b(?:workout|training session|session)\b.*\b(?:on (?:the |my )?(?:screen|pc|computer)|"
                           rf"with (?:the )?companion)\b|\b(?:coach|train) me\b.*\b(?:on (?:the |my )?screen)\b", re.I)),
    ("play", re.compile(r"\blet'?s play\b|\bplay (?:a |some )?(?:game|trivia|rock,? paper,? scissors)\b", re.I)),
)


# How Whisper wrote "(open) Command Center" in his real lines (2026-11-12..15): "Open Community Centre.", "Open Comment
# Center.", "Opinion Command Center.", "Command. Center.", "Enjoy. Open comments into."; and "open up the hub" (the Hub
# window is the Command Center). Whole sentences only (the last one he said wins), so "the community centre opens at 9"
# or "comment on the center" are never a screen request.
_CC_NAME = r"(?:comm?(?:and|ands|ent|ents|unity|on)\s*[.,]?\s*cent(?:er|re)s?|comments?\s+into|(?:the\s+|your\s+|my\s+)?hub)"
_CC_HEARD = re.compile(r"(?:(?:hey|hi|yo)[,.\s]+)?(?:jarvis[,.\s]+)?(?:(?:please|now|okay|ok|go ahead and|can you|could you)\s+)*"
                       r"(?:open(?:\s+up)?|opinion|opening|show(?:\s+me)?|pull\s+up|bring\s+up|launch)?\s*[.,]?\s*"
                       r"(?:the\s+|your\s+|my\s+)?" + _CC_NAME + r"(?:\s+(?:now|please))?[.!?]*", re.I)


def _cc_heard(text: str) -> bool:
    """The Command Center asked for, as he says it and as it gets heard. 'the hub' needs an open-word in front."""
    for part in reversed([p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if p.strip()]):
        if _CC_HEARD.fullmatch(part):
            asked = bool(re.search(r"\b(?:open|opinion|opening|show|pull|bring|launch)\b", part, re.I))
            return asked or bool(re.search(r"\bcommand\b", part, re.I) and not re.search(r"\bhub\b", part, re.I))
        if not re.fullmatch(r"(?:enjoy|okay|ok|yes|yeah|alright|all right|now|please|hey|jarvis)[.!,]?", part, re.I):
            break
    whole = re.sub(r"(?<=\w)[.,]\s+(?=cent)", " ", text.strip(), flags=re.I)      # "Command. Center."
    return whole != text.strip() and bool(_CC_HEARD.fullmatch(whole))


_FORWARD = re.compile(r"(?:(?:hey\s+)?jarvis[,.\s]+)?(?:(?:yes|yeah|okay|ok|now|please|can you|could you)[,.\s]+)*"
                      r"(?:bring|put|move|pull|get)\s+(?P<what>it|that|the window|(?:the\s+)?(?:world\s+)?intelligence|"
                      r"(?:the\s+)?(?:command\s+cent(?:er|re)|dashboard|hub))\s+"
                      r"(?:to the front|(?:up\s+)?front|forward|in front|up)(?:\s+(?:so i can see it|please|now))?[.!?]*"
                      r"|(?:no[,.\s]+)?i\s+(?:don'?t|do not|can'?t|cannot)\s+see\s+it(?:\s+on my screen)?[.!?]*", re.I)


def _bring_forward(t: str) -> str:
    """Which own window "bring it forward" / "I don't see it" means: the one named, or the one he opened just now. ''."""
    m = _FORWARD.fullmatch(t.strip())
    if not m:
        return ""
    what = (m.group("what") or "").casefold()
    if "intelligence" in what:
        return "World Intelligence"
    if any(w in what for w in ("command", "dashboard", "hub")):
        return "the Command Center"
    return recent_own()          # "it": only right after he opened one of Jarvis's own windows


def _shown_line(what: str, did: str) -> str:
    """Say it is up only when the Hub window is really in front (checked); otherwise say so plainly."""
    seen = in_front()
    if seen is False:
        front()                                         # once more: windows behind a full-screen app need a second push
        seen = in_front(1.5)
    if seen is False:
        return (f"I {did} {what}, but it isn't in front of your other windows, sir. "
                f"It's in the Jarvis window on your taskbar.")
    name = what[:1].upper() + what[1:]
    return f"{name} is up on your screen, sir." if did == "opened" else f"{name} is in front now, sir."


def own_screen(text: str) -> str | None:
    """Handle a request for the dashboard / on-screen workout / games on this PC; None when it isn't one."""
    t = (text or "").strip()
    if not t or re.search(r"\b(?:close|hide|stop|cancel)\b", t, re.I):
        return None
    if _cc_heard(t):
        t = "open the command center"
    forward = _bring_forward(t)
    if forward:
        if not running():
            return None
        front()
        return _shown_line(forward, "brought")
    robot_requests = (
        ("home", r"(?:show me|go to|visit) your (?:bedroom|room|home)"),
        ("sleep", r"(?:you|jarvis|robot).*(?:go to bed|take a nap)"),
        ("taskbar", r"sit on (?:the |my )?taskbar"),
        ("search", r"(?:sit|hang out|perch).*(?:windows search|search bar)"),
        ("folder", r"peek (?:out )?(?:from )?behind (?:the |that |a )?folder"),
        ("jetpack", r"(?:use|activate|try|show me) your jetpack"),
        ("catch", r"(?:catch this ball|throw you a ball)"),
        ("juggle", r"(?:let.s juggle|juggle with me)"),
    )
    for action,pattern in robot_requests:
        if re.search(pattern,t,re.I) and companion_action(action):
            return "Right away, sir."  # request accepted; no fabricated completion/result
    for what, rx in _OWN:
        if rx.search(t):
            if what == "dashboard":
                if dashboard():
                    return _shown_line("the Command Center", "opened")
                from tools.interface import open_jarvis_page
                return open_jarvis_page("dashboard")
            if not mode(what):
                return None
            return ("Today's workout is up on the screen. I'll call out each set and time your rests." if what == "workout"
                    else "Games are up on the screen: trivia, rock-paper-scissors, or a reflex test. Choose your humiliation.")
    return None
