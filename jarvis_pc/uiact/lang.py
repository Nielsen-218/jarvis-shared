"""The UI action language: the only thing a teacher model may write for mouse/keyboard/window skills.

One action per line, verbs from a closed list, arguments in double quotes, control NAMES never coordinates:

    click "Save"                  press a button / menu item / tab by its visible name
    set "Name" "Ada"              put text into the text field called Name
    type "hello"                  type keys into whatever has focus
    check "Remember me"           turn a checkbox on        (uncheck "..." turns it off)
    select "green"                choose an item in a list or combo box
    key enter                     one key: enter, tab, escape, up, down, left, right, space, f1-f12
    hotkey ctrl+s                 modifiers ctrl/alt/shift + one key
    wait 2                        seconds (0.2 to 30)
    read "Status"                 print the text of the control whose name contains this
    tap "Add"                     click the word or label READ FROM THE SCREEN (local OCR of this window only), for apps whose
                                  controls UI Automation cannot see; still a name, never a coordinate
    move window 100 80            top-left corner, in pixels
    resize window 640 480         width and height, in pixels
    close window                  ask the window to close (like its X button)

Anything else is rejected before it can run. {placeholders} inside the quotes are filled in later (see learning.fill).
Dangerous things are not in the language at all: no run/start/open program, no shell, no screenshots of other windows, no
"click at x,y", and nothing can name a window other than the one the executor is bound to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

KEYS = {"enter", "tab", "escape", "esc", "up", "down", "left", "right", "space", "home", "end", "pageup", "pagedown", "delete", "backspace"} | {
    f"f{i}" for i in range(1, 13)}
MODS = {"ctrl", "alt", "shift"}
Q = r'"((?:[^"\\]|\\.)*)"'

_PATTERNS = [
    ("click", re.compile(rf"^click {Q}$", re.I)),
    ("set", re.compile(rf"^set {Q} {Q}$", re.I)),
    ("type", re.compile(rf"^type {Q}$", re.I)),
    ("check", re.compile(rf"^check {Q}$", re.I)),
    ("uncheck", re.compile(rf"^uncheck {Q}$", re.I)),
    ("select", re.compile(rf"^select {Q}$", re.I)),
    ("read", re.compile(rf"^read {Q}$", re.I)),
    ("tap", re.compile(rf"^tap {Q}$", re.I)),
    ("key", re.compile(r"^key ([a-z0-9]+)$", re.I)),
    ("hotkey", re.compile(r"^hotkey ((?:(?:ctrl|alt|shift)\+)+[a-z0-9]+)$", re.I)),
    ("wait", re.compile(r"^wait (\d+(?:\.\d+)?)$", re.I)),
    ("move", re.compile(r"^move window (-?\d{1,5}) (-?\d{1,5})$", re.I)),
    ("resize", re.compile(r"^resize window (\d{2,5}) (\d{2,5})$", re.I)),
    ("close", re.compile(r"^close window$", re.I)),
]


@dataclass
class Step:
    verb: str
    args: Tuple[str, ...]
    text: str


class UIParseError(ValueError):
    pass


def _unquote(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse(line: str) -> Step:
    """One action line -> a Step, or UIParseError saying why it is not allowed."""
    raw = (line or "").strip()
    if not raw or len(raw) > 300:
        raise UIParseError("empty or too long")
    for verb, rx in _PATTERNS:
        m = rx.match(raw)
        if not m:
            continue
        quoted = verb in ("click", "set", "type", "check", "uncheck", "select", "read", "tap")
        args = tuple(_unquote(g) if quoted else g for g in m.groups())
        step = Step(verb, args, raw)
        _check(step)
        return step
    raise UIParseError(f"not an allowed action: {raw[:80]}")


def _check(step: Step) -> None:
    if step.verb == "key" and step.args[0].lower() not in KEYS:
        raise UIParseError(f"unknown key {step.args[0]!r}")
    if step.verb == "hotkey":
        parts = step.args[0].lower().split("+")
        key = parts[-1]
        if not set(parts[:-1]) <= MODS or len(set(parts[:-1])) != len(parts[:-1]):
            raise UIParseError("hotkey modifiers must be ctrl, alt or shift, once each")
        if not (len(key) == 1 or key in KEYS):
            raise UIParseError(f"unknown hotkey key {key!r}")
        mods_used = set(parts[:-1])
        if "alt" in mods_used and key in ("f4", "tab", "esc", "escape"):
            raise UIParseError("that hotkey would close or switch programs")
        if {"ctrl", "alt"} <= mods_used and key in ("delete", "backspace"):
            raise UIParseError("that is the secure attention sequence; never sent")
        if {"ctrl", "shift"} <= mods_used and key in ("esc", "escape"):
            raise UIParseError("that opens Task Manager; never sent")
    if step.verb == "wait" and not (0.2 <= float(step.args[0]) <= 30):
        raise UIParseError("wait must be between 0.2 and 30 seconds")
    if step.verb in ("move", "resize") and any(abs(int(a)) > 10000 for a in step.args):
        raise UIParseError("window size/position out of range")
    for a in step.args:
        if step.verb in ("click", "set", "type", "check", "uncheck", "select", "read", "tap") and "\n" in a:
            raise UIParseError("no line breaks inside a quoted argument")


def split_hotkey(spec: str) -> Tuple[List[str], str]:
    parts = spec.lower().split("+")
    return parts[:-1], parts[-1]


def to_pywinauto_keys(spec: str) -> str:
    """'ctrl+s' -> '^s'; named keys become {ENTER} style."""
    mods, key = split_hotkey(spec)
    prefix = "".join({"ctrl": "^", "alt": "%", "shift": "+"}[m] for m in mods)
    named = {"enter": "{ENTER}", "tab": "{TAB}", "escape": "{ESC}", "esc": "{ESC}", "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}",
             "right": "{RIGHT}", "space": "{SPACE}", "home": "{HOME}", "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}",
             "delete": "{DELETE}", "backspace": "{BACKSPACE}"}
    k = named.get(key) or (f"{{{key.upper()}}}" if key.startswith("f") and key[1:].isdigit() else key)
    return prefix + k


def rules_for_teacher() -> str:
    return _TEACHER_RULES


_TEACHER_RULES = """You write UI actions for a PC assistant that controls ONE already-open window. Reply with ONLY the actions, one per line, no
explanations, no code fences. The allowed actions are exactly these (anything else is rejected):
click "Name"          press a button, menu item or tab by its visible name
set "Field" "text"    put text into the text field called Field
type "text"           type keys into whatever has focus
check "Label"         turn a checkbox on (uncheck "Label" turns it off)
select "Item"         choose an item in a list
key enter             one key: enter, tab, escape, up, down, left, right, space, home, end, pageup, pagedown, f1-f12
hotkey ctrl+s         modifiers ctrl/alt/shift plus one key
wait 2                wait N seconds (a control that is greyed out may become available after a moment)
read "Text"           print the text of the control whose name contains this
move window X Y       move the window's top-left corner
resize window W H     set the window's width and height
close window          close the window
Some windows draw their own grid (a spreadsheet's cells) and list no control for each cell: for those use the keyboard. The cell cursor starts
in the first cell; key down/up/left/right moves it, type writes into the current cell, a formula starts with =, and key enter confirms.
A game or any task that says to use keys is played with key lines only (one key per line, for example key end then key pagedown): do not click
buttons for it, and do not use type for arrow or navigation keys.
Rules: use the control NAMES the task gives you; never coordinates. Values the task gives in {braces} must be written exactly like
that, inside the quotes, e.g. click "{label}": the real value is filled in later. Never write a real name that the task gave as a
{placeholder}.
Examples (different tasks from yours; do not copy them):
Task: press the button called {name}
click "{name}"

Task: switch on the option called {option} and then save with the keyboard
check "{option}"
hotkey ctrl+s

Task: with the keyboard only, move the cursor to the last column and then to the first row
key end
key pageup
"""
