"""School subjects for gaming (blueprint area 19): control a game window with the keyboard, graded on what the game itself logged.

The "game" is a small board inside the school's own practice window (`uiact/practice_app.py`, game mode): arrow keys move the player one
square, Home/End jump to the left/right edge, PageUp/PageDown jump to the top/bottom edge, and the window logs every move. Jarvis has to
choose the right keys and send them to the right window; the game's own log (never a model) is the ground truth. It is a stand-in game,
so no installed game and none of the owner's windows are touched. The key sequences do not depend on the board size, so a learned
playbook works on every variant.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .curriculum import Context, HIGH_SCHOOL, Subject, Task
from .ui_subjects import _lines, _none, _spawn, available

KEYS_NOTE = ("(play it with keyboard key lines only, never click anything: key left, key right, key up, key down move one square; key home and key end jump to the "
             "left and right edges; key pageup and key pagedown jump to the top and bottom edges)")


def _moves(log: str) -> List[Tuple[int, int]]:
    out = []
    for ln in _lines(log):
        if ln.startswith("move:"):
            x, y = ln[5:].split(",")
            out.append((int(x), int(y)))
    return out


def _corner(c: Context) -> Task:
    ask = f"in the game window the player starts in the top-left corner; move the player to the bottom-right corner {KEYS_NOTE}"
    if not available():
        return _none(ask, {})
    size = c.rng.randint(4, 7)
    title, log, _cfg = _spawn(c, game=True, game_size=size)

    def check():
        m = _moves(log)
        ok = bool(m) and m[-1] == (size - 1, size - 1)
        return ok, "the player is in the bottom-right corner" if ok else f"the player should end on {size - 1},{size - 1}, the game saw {m[-1] if m else 'no moves'}"
    return Task(ask, {}, [], check, 1, kind="ui", ui_title=title)


def _tour(c: Context) -> Task:
    ask = (f"in the game window the player starts in the top-left corner; visit the bottom-left corner first, then finish in the "
           f"top-right corner {KEYS_NOTE}")
    if not available():
        return _none(ask, {})
    size = c.rng.randint(4, 7)
    title, log, _cfg = _spawn(c, game=True, game_size=size)

    def check():
        m = _moves(log)
        if not m:
            return False, "the game saw no moves"
        if m[-1] != (size - 1, 0):
            return False, f"the player should finish on {size - 1},0, it ended on {m[-1][0]},{m[-1][1]}"
        if (0, size - 1) not in m:
            return False, "the player never visited the bottom-left corner"
        return True, "bottom-left first, then the top-right corner"
    return Task(ask, {}, [], check, 1, kind="ui", ui_title=title)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    mk = lambda i, t, f, al, **k: Subject(i, t, hs, f, al, kind="ui", needs_windows=True, **k)  # noqa: E731
    return [
        mk("game_corner", "steer a game piece to a corner", _corner, ("game controls", "keyboard game"), needs=("ui_hotkey_save",)),
        mk("game_tour", "steer a game piece through two corners", _tour, ("game route", "plan a path"), needs=("game_corner",)),
    ]
