"""One graphics card, one brain: his turn goes first, background model work waits for a gap.

A port of Leon's local-inference scheduler, cut to what JARVIS needs. A FOREGROUND turn (Brain.ask, from the quick
answers through the PC layer to the last word of the brain) holds the gate for the whole turn, not only during each model
call: the journal notes used to start a model call in the gap between the gateway's call and the brain's, and the owner's
next call waited behind it. BACKGROUND work (the journal notes, the conversation summary, the miss lessons) takes the one
background slot only while no turn is active or reserved (the wake word reserves the card while he is still talking)
and never while a game runs (warmth.gaming: Jarvis adds no load to a game).

A call already running is never cut off: a turn that starts meanwhile simply waits for it on the model lock
(core.intelligence), and the job sees `slot.cancelled()` before it starts another. Waiting is bounded by the caller's
timeout; a job that could not start in time gets a slot whose `ok` is False and runs nothing.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

POLL_S = 2.0          # a waiting job looks again this often (a game starts and ends without telling us)
RESERVE_S = 25.0      # the wake word holds the card this long for the turn he is still speaking

_cond = threading.Condition()
_foreground = 0                  # turns being answered now (one Brain per person and channel: several may overlap)
_reserved_until = 0.0            # monotonic: a turn is about to start (wake word heard)
_background: str | None = None   # the background job holding the slot, by name


def _gaming() -> bool:
    try:
        from core import warmth
        return bool(warmth.gaming())
    except Exception:  # noqa: BLE001  (no game probe: nothing to hold for)
        return False


GAMING = _gaming      # replaced in tests (tests/conftest.py): the suite never depends on what the PC is running


def _turn_now() -> bool:
    """With _cond held."""
    return _foreground > 0 or time.monotonic() < _reserved_until


def busy() -> bool:
    """A turn of his is being answered, or is about to be."""
    with _cond:
        return _turn_now()


def running() -> str | None:
    """The background job holding the slot now, by name (None when free)."""
    return _background


class Slot:
    """What background() hands its job: `ok` (it may run) and `cancelled()` (a turn began: start no further call)."""

    def __init__(self, name: str, ok: bool):
        self.name, self.ok = name, ok

    def __bool__(self) -> bool:
        return self.ok

    @staticmethod
    def cancelled() -> bool:
        return busy()


@contextmanager
def foreground():
    """Around one whole turn. Never waits: a running background call is waited for on the model lock instead."""
    global _foreground, _reserved_until
    with _cond:
        _foreground += 1
        announced = _reserved_until          # the reservation this turn may be the answer to
    try:
        yield
    finally:
        with _cond:
            _foreground -= 1
            # The turn the wake word announced has been answered. A reservation made while it ran (the wake word for
            # the next turn, another Brain's) stays until that turn ends or runs out.
            if not _foreground and _reserved_until == announced:
                _reserved_until = 0.0
            _cond.notify_all()


def reserve(seconds: float = RESERVE_S) -> None:
    """A turn is coming (the wake word): background work starts nothing new until it ends or `seconds` pass."""
    global _reserved_until
    with _cond:
        _reserved_until = max(_reserved_until, time.monotonic() + max(0.0, seconds))


def _left(deadline: float | None) -> float | None:
    return None if deadline is None else deadline - time.monotonic()


def _wait(deadline: float | None) -> bool:
    """With _cond held: wait a little. False once the deadline has passed."""
    left = _left(deadline)
    if left is not None and left <= 0:
        return False
    reserved = _reserved_until - time.monotonic()
    step = POLL_S if reserved <= 0 else min(POLL_S, reserved + 0.01)
    _cond.wait(step if left is None else min(step, left))
    return True


def _claim(name: str, deadline: float | None) -> bool:
    global _background
    while True:
        with _cond:
            while _turn_now() or _background is not None:
                if not _wait(deadline):
                    return False
        if not GAMING():                     # outside the lock: the game probe lists processes and takes a moment
            with _cond:
                if not _turn_now() and _background is None:
                    _background = name
                    return True
            continue
        with _cond:
            if not _wait(deadline):          # a game: look again in a moment
                return False


@contextmanager
def background(name: str, timeout: float | None = None):
    """Run a background job's model work: waits (at most `timeout` seconds; None for as long as it takes) until no turn
    is active or reserved, no game runs and no other background job holds the slot. Yields a Slot; `if slot:` it may run.
    The slot is released when the block ends, however it ends."""
    global _background
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    got = _claim(name, deadline)
    try:
        yield Slot(name, got)
    finally:
        if got:
            with _cond:
                _background = None
                _cond.notify_all()
