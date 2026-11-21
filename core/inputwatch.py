"""Is the owner using this PC? Keyboard and mouse AND game controllers.

Windows' idle timer (GetLastInputInfo) does not see gamepads, so someone playing with a controller looks "away" to it.
This module polls XInput (Xbox-style pads, most PC controllers, Steam Input) and the classic joystick API (other pads,
wheels, sticks) a few times a second in one background thread and remembers when a controller last did something.

Only real use counts: a button, a trigger, a stick moved past a dead zone, or a controller being plugged in. A pad lying
on the desk with a slightly drifting stick is not "use". Everything here only reads input state; it never sends any.
"""
import ctypes
import os
import threading
import time

POLL_SECONDS = 0.25
# A connected controller is read every poll; an EMPTY slot is not. Microsoft's XInput advice: don't call XInputGetState
# for an empty user slot every frame, check for new controllers every few seconds. The classic joystick API is worse in
# one place: asked about joystick 0 with no device there, winmm makes DirectInput rebuild its device list (SetupAPI,
# 18-39 ms, measured: only slot 0 does it, every time; slots 1-15 answer in ~0.01 ms). Asking all 16 slots 4 times a
# second was ~350,000 rebuilds a day inside Jarvis, and it died in one of them (2026-11-11 21:25, 0xC0000374: crash.log's
# faulthandler names inputwatch's joyGetPosEx; the heap record is an overrun of the buffer DirectInput was growing; at
# 16:41 a helper thread joyGetPosEx had started faulted on the same block). So an empty joystick 0 is asked only after an
# input device arrives or leaves (Windows' raw-input device count) and as a slow fallback.
XINPUT_EMPTY_EVERY = 3.0     # empty XInput slots
JOY_EMPTY_EVERY = 3.0        # empty winmm slots 1-15 (cheap)
JOY0_EMPTY_EVERY = 600.0     # an empty winmm slot 0 (the rebuild), besides the checks after a device arrives
ARRIVAL_CHECKS = (0.0, 1.0, 3.0, 8.0)   # after the device count changes: a new pad may answer winmm a moment later
REBUILD_GAP = 1.0            # never two slot-0 rebuilds closer than this, even with a device count that keeps changing
RETRY_AFTER = 1.0            # a connected pad whose read failed is asked again this soon
GONE_AFTER = 2.0             # a pad missing from the reads this long is unplugged; one failed read is not
API_BACKOFF = (1.0, 5.0, 30.0, 180.0, 600.0)   # an API whose call raised (ctypes reports a crash inside the DLL as
                                               # OSError) rests this long, longer each time in a row; a good pass resets it
STICK_MOVE = 6000          # XInput sticks are -32768..32767; drift and noise stay far below this
TRIGGER_MOVE = 40          # XInput triggers are 0..255
JOY_MOVE = 6000            # winmm axes are 0..65535

_lock = threading.Lock()
_thread = None
_started = None            # when watching began (before that, nothing is known about controllers)
_last_use = None           # monotonic time of the last controller use seen
_uses = 0                  # how many uses have been seen (a change in this means "someone touched a controller")
_last = {}                 # device -> last state that counted
_missing = {}              # device -> when it was first missing from the reads (see GONE_AFTER)


class _GAMEPAD(ctypes.Structure):
    _fields_ = [("buttons", ctypes.c_ushort), ("lt", ctypes.c_ubyte), ("rt", ctypes.c_ubyte),
                ("lx", ctypes.c_short), ("ly", ctypes.c_short), ("rx", ctypes.c_short), ("ry", ctypes.c_short)]


class _XSTATE(ctypes.Structure):
    _fields_ = [("packet", ctypes.c_uint), ("pad", _GAMEPAD)]


class _JOYINFOEX(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("size", "flags", "x", "y", "z", "r", "u", "v", "buttons", "button_number", "pov",
                                             "reserved1", "reserved2")]


class _RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("device", ctypes.c_void_p), ("type", ctypes.c_uint)]


def _load():
    xinput = joy = None
    if os.name != "nt":
        return xinput, joy
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            xinput = ctypes.WinDLL(name)
            break
        except OSError:
            continue
    # winmm's joyGetPosEx is NOT loaded by default any more. It corrupted this process's heap three times while the owner
    # played with a controller (0xC0000374 on 2026-11-11 21:25 and again 2026-11-12 20:36 with the reduced polling; the
    # crash log names this call each time), and each crash took Jarvis down until someone restarted him. XInput covers
    # Xbox-style pads, which is what "is he using a controller" needs. JARVIS_JOY_WINMM=on brings the old path back.
    if os.getenv("JARVIS_JOY_WINMM", "").strip().lower() in ("1", "on", "true", "yes"):
        try:
            joy = ctypes.WinDLL("winmm")
        except OSError:
            pass
    return xinput, joy


class Slots:
    """Which slots of one controller API to read on this poll. A connected slot: every poll. An empty one: every
    `every` s, or soon after a device arrived or left (arrived()); the `costly` ones (winmm's joystick 0) only every
    `costly_every` s and on those arrival checks, never closer than REBUILD_GAP. A connected slot whose read just
    failed: once more after RETRY_AFTER. Nothing is marked done until a pass finishes (done()), so a pass cut short by
    an error is not counted as a check."""

    def __init__(self, count: int, every: float, costly=(), costly_every: float = 0.0):
        self.count, self.every = count, every
        self.costly, self.costly_every = set(costly), costly_every
        self.live: set[int] = set()
        self.retry: dict[int, float] = {}     # slot -> when to ask it once more
        self.cheap_at = None                  # when the empty cheap slots were last read in a finished pass
        self.costly_at = None                 # the same for the costly ones
        self.arrivals: list[float] = []       # when to look at every slot after a device arrived or left

    def arrived(self, now: float) -> None:
        self.arrivals = [now + d for d in ARRIVAL_CHECKS]

    def due(self, now: float):
        """-> (slots to read now, the plan to hand to done())."""
        arrival = bool(self.arrivals) and now >= self.arrivals[0]
        cheap = arrival or self.cheap_at is None or now - self.cheap_at >= self.every
        costly = bool(self.costly) and (self.costly_at is None or now - self.costly_at >= self.costly_every
                                        or (arrival and now - self.costly_at >= REBUILD_GAP))
        ids = set(self.live) | {s for s, at in self.retry.items() if now >= at}
        if cheap:
            ids |= set(range(self.count)) - self.costly
        if costly:
            ids |= self.costly
        return sorted(ids), (cheap, costly, arrival)

    def seen(self, slot: int, connected: bool, now: float = 0.0) -> None:
        if connected:
            self.live.add(slot)
            self.retry.pop(slot, None)
        elif slot in self.live:
            self.live.discard(slot)
            self.retry[slot] = now + RETRY_AFTER          # one glitched read is not an unplug: ask once more soon

    def done(self, now: float, plan) -> None:
        cheap, costly, arrival = plan
        if cheap:
            self.cheap_at = now
        if costly:
            self.costly_at = now
        if arrival:
            self.arrivals = [t for t in self.arrivals if t > now]
        self.retry = {s: at for s, at in self.retry.items() if at > now}   # asked this pass (or just scheduled)


def new_slots() -> dict:
    return {"xinput": Slots(4, XINPUT_EMPTY_EVERY),
            "joy": Slots(16, JOY_EMPTY_EVERY, costly={0}, costly_every=JOY0_EMPTY_EVERY),
            "devices": None, "resting": {}, "fails": {}}


def device_count() -> int:
    """How many raw input devices Windows knows (keyboards, mice and HID devices, controllers included). A few
    microseconds to read; it changes when a device arrives or leaves, which is when an empty slot may have filled."""
    if os.name != "nt":
        return -1
    n = ctypes.c_uint(0)
    ctypes.windll.user32.GetRawInputDeviceList(None, ctypes.byref(n), ctypes.sizeof(_RAWINPUTDEVICELIST))
    return n.value


def _read_xinput(xinput, ids, out, slots, now):
    for i in ids:
        st = _XSTATE()
        ok = xinput.XInputGetState(i, ctypes.byref(st)) == 0
        if slots:
            slots["xinput"].seen(i, ok, now)
        if ok:
            p = st.pad
            out[("xinput", i)] = (p.buttons, p.lt, p.rt, p.lx, p.ly, p.rx, p.ry)


def _read_joy(joy, ids, out, slots, now):
    for j in ids:
        ji = _JOYINFOEX(ctypes.sizeof(_JOYINFOEX), 255)       # JOY_RETURNALL
        ok = joy.joyGetPosEx(j, ctypes.byref(ji)) == 0
        if slots:
            slots["joy"].seen(j, ok, now)
        if ok:
            out[("joy", j)] = (ji.buttons, ji.pov, ji.x, ji.y, ji.z, ji.r, ji.u, ji.v)


def read_controllers(xinput, joy, slots: dict | None = None, now: float | None = None) -> dict:
    """device -> (buttons, axes...) for every connected controller. With `slots` (the watcher's own, see Slots), empty
    slots are read only now and then, and an API whose call raised rests for API_BACKOFF while the other one is still
    read; without, every slot is read (one-off checks) and an error is raised."""
    out = {}
    now = time.monotonic() if now is None else now
    if slots is None:
        if xinput:
            _read_xinput(xinput, range(4), out, None, now)
        if joy:
            _read_joy(joy, range(16), out, None, now)
        return out
    count = device_count()
    if count != slots["devices"]:
        if slots["devices"] is not None:              # a device arrived or left: look at every slot soon
            slots["xinput"].arrived(now)
            slots["joy"].arrived(now)
        slots["devices"] = count
    for name, api, read in (("xinput", xinput, _read_xinput), ("joy", joy, _read_joy)):
        if not api or now < slots["resting"].get(name, 0.0):
            continue
        ids, plan = slots[name].due(now)
        try:
            read(api, ids, out, slots, now)
        except OSError:                               # never hammer a call that just failed (the crash path)
            fails = slots["fails"].get(name, 0)
            slots["resting"][name] = now + API_BACKOFF[min(fails, len(API_BACKOFF) - 1)]
            slots["fails"][name] = fails + 1
            continue
        slots["fails"][name] = 0
        slots[name].done(now, plan)
    return out


def used(device, old, new) -> bool:
    """Did this change come from a person (not stick drift)?"""
    if old is None:
        return True                                           # a controller was just connected
    if device[0] == "xinput":
        if old[0] != new[0]:
            return True
        if any(abs(a - b) > TRIGGER_MOVE for a, b in zip(old[1:3], new[1:3])):
            return True
        return any(abs(a - b) > STICK_MOVE for a, b in zip(old[3:], new[3:]))
    if old[0] != new[0] or old[1] != new[1]:                   # buttons or the d-pad
        return True
    return any(abs(a - b) > JOY_MOVE for a, b in zip(old[2:], new[2:]))


def observe(states: dict, now: float) -> None:
    """Fold one reading into the record (also used by the tests)."""
    global _last_use, _uses
    with _lock:
        for device, state in states.items():
            _missing.pop(device, None)
            if used(device, _last.get(device), state):
                _last[device] = state
                _last_use, _uses = now, _uses + 1
        # Unplugged: forget it, so plugging it back in counts as use. Only after GONE_AFTER: one failed read of a pad
        # lying on the desk made its next read a "plug-in", i.e. use (review 2026-11-11).
        for device in [d for d in _last if d not in states]:
            if now - _missing.setdefault(device, now) >= GONE_AFTER:
                _last.pop(device, None)
                _missing.pop(device, None)


def _loop():
    xinput, joy = _load()
    if not (xinput or joy):
        return
    first = True
    slots = new_slots()
    while True:
        try:
            states = read_controllers(xinput, joy, slots)
        except OSError:
            # A half-done read says nothing about who is here. Fed as "no controllers", observe() forgot every pad and
            # counted the next read as a fresh plug-in, i.e. "use" (review 2026-11-11): skip this poll instead.
            time.sleep(POLL_SECONDS)
            continue
        if first:
            with _lock:                                       # present when watching began: baseline, not a use
                _last.update(states)
            first = False
        else:
            observe(states, time.monotonic())
        time.sleep(POLL_SECONDS)


def start() -> None:
    global _thread, _started
    with _lock:
        if _thread is not None:
            return
        _started = time.monotonic()
        _thread = threading.Thread(target=_loop, name="controller-watch", daemon=True)
        _thread.start()


def keyboard_mouse_tick() -> int:
    if os.name != "nt":
        return 0

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LII(ctypes.sizeof(LII), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


def keyboard_mouse_idle() -> float:
    if os.name != "nt":
        return 0.0
    tick = keyboard_mouse_tick()
    return ((ctypes.windll.kernel32.GetTickCount() - tick) & 0xFFFFFFFF) / 1000.0


def controller_idle() -> float:
    """Seconds since a controller was last used. Until one is seen, the time since watching began (unknown = not idle)."""
    start()
    with _lock:
        ref = _last_use if _last_use is not None else _started
    return time.monotonic() - ref


def controller_used_within(seconds: float) -> bool:
    """True only when a controller was SEEN in use in the last `seconds`. Unlike controller_idle(), a controller never
    seen since watching began counts as unused (the mission worker must not wait 10 minutes after every restart)."""
    start()
    with _lock:
        last = _last_use
    return last is not None and time.monotonic() - last < seconds


def _turn_dir():
    """Touched on every request to Jarvis (voice or phone): talking to him from the couch or the phone is presence too
    (2026-11-08: a phone request waited 121 s behind practice that thought nobody was there). data/learning is synced
    between the PC and the laptop, so each machine writes ITS OWN file (one writer per synced file: no conflict copies)
    and the PC reads both. The practice watcher runs from another folder and is told the live one by JARVIS_TURN_DIR."""
    from pathlib import Path
    explicit = os.getenv("JARVIS_TURN_DIR")
    if explicit:
        return Path(explicit)
    from core import storage
    return storage.DATA / "learning"


def turn_file():
    try:
        from core import pcbridge
        host = "pc" if pcbridge.local_pc() else "server"
    except Exception:  # noqa: BLE001
        host = "pc" if os.name == "nt" else "server"
    return _turn_dir() / f"owner-turn-{host}"


def note_turn() -> None:
    try:
        path = turn_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def _turn_mtime() -> float:
    newest = 0.0
    for host in ("pc", "server"):
        try:
            newest = max(newest, (_turn_dir() / f"owner-turn-{host}").stat().st_mtime)
        except OSError:
            pass
    return newest


def turn_idle() -> float:
    at = _turn_mtime()
    return time.time() - at if at else float("inf")


def idle_seconds() -> float:
    """Seconds since the owner last used the keyboard, mouse, a controller OR asked Jarvis something (voice/phone)."""
    return min(keyboard_mouse_idle(), controller_idle(), turn_idle())


def activity_token():
    """Changes whenever the owner touches the keyboard, mouse or a controller (compare two tokens to see if they did)."""
    start()
    with _lock:
        uses = _uses
    return (keyboard_mouse_tick(), uses, _turn_mtime())
