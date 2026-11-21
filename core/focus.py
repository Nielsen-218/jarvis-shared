"""Focus mode: Pomodoro-style blocks (default 25 minutes of focus, 5 of break, 4 rounds) with Jarvis calling the
switches. While a focus block runs, the PC companion keeps quiet: no quips, break nudges or welcome-backs."""
import threading
import time

from core.words import plural

_lock = threading.Lock()
_s: dict = {"phase": "off"}   # phase: off | focus | break
_timer: threading.Timer | None = None
announce = None               # set to tools.system.announce by default; tests replace it


def _say(text: str) -> None:
    fn = announce
    if fn is None:
        from tools import system
        fn = system.announce
    try:
        fn(text)
    except Exception as e:
        print(f"[focus] {e}")


def status() -> dict:
    with _lock:
        s = dict(_s)
    if s["phase"] != "off":
        s["left"] = max(0, round(s["ends"] - time.time()))
    return s


def _publish() -> None:
    from core import hud
    hud._send({"type": "focus", "value": status()})
    hud.badges(focus="FOCUS" if _s["phase"] == "focus" else "BREAK" if _s["phase"] == "break" else "")


def _arm(seconds: float) -> None:
    global _timer
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(seconds, _next)
    _timer.daemon = True
    _timer.start()


def _next() -> None:
    with _lock:
        s = _s
        if s["phase"] == "focus":
            s["done"] += 1
            if s["done"] >= s["rounds"]:
                total = s["done"] * s["focus_min"]
                s.clear()
                s["phase"] = "off"
                msg = f"Focus session complete: {plural(total, 'minute')} of proper work. I'm rather proud of us."
                end = True
            else:
                s.update(phase="break", ends=time.time() + s["break_min"] * 60)
                msg = (f"Focus block {s['done']} done. Take a {s['break_min']}-minute break: stand up, stretch, "
                       f"drink some water.")
                end = False
        elif s["phase"] == "break":
            s.update(phase="focus", ends=time.time() + s["focus_min"] * 60)
            msg = f"Break's over. Block {s['done'] + 1} of {s['rounds']}: {plural(s['focus_min'], 'minute')}. Off you go."
            end = False
        else:
            return
        left = None if end else s["ends"] - time.time()
    if left:
        _arm(left)
    _publish()
    _say(msg)


def start(focus_min: int = 25, break_min: int = 5, rounds: int = 4) -> dict:
    focus_min = max(5, min(int(focus_min or 25), 180))
    break_min = max(1, min(int(break_min or 5), 60))
    rounds = max(1, min(int(rounds or 4), 12))
    with _lock:
        _s.clear()
        _s.update(phase="focus", focus_min=focus_min, break_min=break_min, rounds=rounds, done=0,
                  started=time.time(), ends=time.time() + focus_min * 60)
    _arm(focus_min * 60)
    _publish()
    return status()


def stop() -> dict:
    global _timer
    with _lock:
        was = dict(_s)
        _s.clear()
        _s["phase"] = "off"
    if _timer:
        _timer.cancel()
        _timer = None
    _publish()
    return was


def skip() -> dict:
    """End the current block early and move on (focus -> break, break -> focus)."""
    if _s["phase"] == "off":
        return status()
    _next()
    return status()


def active() -> bool:
    return _s["phase"] == "focus"
