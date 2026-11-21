"""One narrow fact, answered from the PC: is this app open?

"Is Notepad still open?" is a question about the PC's state, not an order. This module recognises ONLY these sentences, whole and
nothing else (after an optional "hey jarvis" / "jarvis" in front and "please" / "sir" / "right now" / "at the moment" behind):

  is <APP> (still|currently|already)? (open|running|up|closed|shut)
  is <APP> (still)? (open|running) or (closed|not)
  do i (still)? have <APP> (open|running)
  did you (close|open|shut) <APP>          did <APP> (close|open)

<APP> is ONE catalogue name, whole and exact (case-insensitive; the same catalogue apps.resolve_app reads): no fuzzy match, no
substring, no alias ("browser"), no name with a character the catalogue's name lacks ("notepad++" is not "notepad"), and no name
two catalogue entries could both be. A sentence with a comma, another clause, a second verb, "and", "then", "if", "so", "but", a
negation, or more than nine words is not a fact. Everything not recognised goes exactly where it went before this module.

The answer is read from the window list the app matcher uses just now, keeping only the windows that are the app's OWN (apps.own_windows:
not a Chrome tab that mentions it). A window that is seen is "open"; none seen is said as what it is, "I don't see a window", never as
"closed": the matcher cannot see every app (a tray-only app, an app whose window text never holds its name). For
"did you close <APP>?" it starts with a clear Yes or No about the state ("Yes, sir, I don't see a Notepad window any more." /
"No, sir, Notepad is still open."), and never says Jarvis did it: whether he did is not something he can verify. No model, no action.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

_CHANNEL = re.compile(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*")
_PREFIX = re.compile(r"^(?:hey\s*,?\s+)?jarvis\s*,?\s+")
_TAIL = re.compile(r"(?:\s*,?\s+(?:please|sir|right now|at the moment))+$")
MAX_WORDS = 9

_ADVERBS = ("", "still", "currently", "already")
_OPEN_WORDS = ("open", "running", "up")
_CLOSED_WORDS = ("closed", "shut")
# a name carrying one of these is not "ONE app": it has another clause, a negation or a second verb in it
_NOT_ONE_NAME = frozenset({"and", "then", "if", "so", "but", "or", "not", "no", "never", "nor", "also", "plus", "please"})
# Names that are not ONE app: "browser" opens whatever the default is, "files" is another word for File Explorer.
_NO_WINDOW_OF_ITS_OWN = frozenset({"browser", "files"})


def _tails_is() -> list:
    """Every way "is <APP> ..." may end, as word tuples, with what it asks: the state wanted."""
    tails = []
    for adverb in _ADVERBS:
        lead = (adverb,) if adverb else ()
        tails += [(lead + (w,), "open") for w in _OPEN_WORDS] + [(lead + (w,), "closed") for w in _CLOSED_WORDS]
    for adverb in ("", "still"):
        lead = (adverb,) if adverb else ()
        for w in ("open", "running"):
            tails += [(lead + (w, "or", end), "open") for end in ("closed", "not")]
    return tails


_TAILS_IS = _tails_is()


def _normalise(said: str) -> str:
    text = _CHANNEL.sub("", said or "").replace("’", "'").casefold().strip()
    return re.sub(r"\s+", " ", text)


def _core(said: str) -> Optional[str]:
    """The sentence without its optional prefix and tail, or None when it is not ONE plain sentence."""
    text = _normalise(said)
    if len(text.split()) > MAX_WORDS:
        return None
    text = re.sub(r"[.?!\s]+$", "", text)
    text = _PREFIX.sub("", text)
    previous = None
    while previous != text:                      # "... open right now, sir"
        previous = text
        text = _TAIL.sub("", text).strip()
    if not text or re.search(r"[^a-z0-9 '.+&-]", text) or re.search(r"[.?!]", re.sub(r"(?<=\w)\.(?=\w)", "", text)):
        return None                              # a comma, a second sentence or any other mark: not this sentence
    return text


def _catalogue() -> set:
    """The catalogue's names (Start Menu, Steam, Store and Windows' built-ins), as apps.resolve_app reads them; empty if unreadable."""
    try:
        from tools import apps
        return set(apps._installed()) | set(apps.BUILTINS)
    except Exception:  # noqa: BLE001 - the laptop has no Start Menu: Windows' built-ins are all it knows
        try:
            from tools import apps
            return set(apps.BUILTINS)
        except Exception:  # noqa: BLE001
            return set()


def _flat(name: str) -> str:
    return re.sub(r"[^\w+]+", "", name.casefold())


def app_named(name: str, names: Optional[set] = None) -> Optional[str]:
    """The ONE catalogue entry `name` is, whole and exactly, or None: not a name, not an alias, not ambiguous."""
    name = " ".join(name.split()).casefold()
    if not name or name in _NO_WINDOW_OF_ITS_OWN or _NOT_ONE_NAME & set(re.findall(r"[a-z0-9']+", name)) or "n't" in name:
        return None
    names = _catalogue() if names is None else names
    exact = {n for n in names if " ".join(n.split()).casefold() == name}
    same_spelling = {n for n in names if _flat(n) == _flat(name)}      # "paint.net" / "paint net" could both be meant: ambiguous
    if len(same_spelling) != 1 or not exact:
        return None
    return next(iter(exact))


def _candidates(words: list) -> list:
    """Every (app text, state wanted, verb asked) a sentence could be read as under the shapes above."""
    out = []
    if len(words) >= 3 and words[0] == "is":
        for tail, want in _TAILS_IS:
            if len(words) > len(tail) + 1 and tuple(words[-len(tail):]) == tail:
                out.append((" ".join(words[1:-len(tail)]), want, ""))
    for head in (("do", "i", "have"), ("do", "i", "still", "have")):
        if len(words) > len(head) + 1 and tuple(words[:len(head)]) == head and words[-1] in ("open", "running"):
            out.append((" ".join(words[len(head):-1]), "open", ""))
    if len(words) >= 4 and words[:2] == ["did", "you"] and words[2] in ("close", "open", "shut"):
        out.append((" ".join(words[3:]), "closed" if words[2] in ("close", "shut") else "open", "did"))
    if len(words) >= 3 and words[0] == "did" and words[-1] in ("close", "open"):
        out.append((" ".join(words[1:-1]), "closed" if words[-1] == "close" else "open", "did"))
    return out


def classify(said: str, names: Optional[set] = None) -> tuple:
    """-> ("fact", {"kind": "app", "app": catalogue name, "want": "open" | "closed", "did": "did" | ""}) or ("other", None)."""
    core = _core(said)
    if core is None:
        return "other", None
    found = {}
    for text, want, did in _candidates(core.split()):
        app = app_named(text, names)
        if app:
            found[(app, want, did)] = True
    if len(found) != 1:
        return "other", None                     # nothing recognised, or two readings: it is not a fact
    app, want, did = next(iter(found))
    return "fact", {"kind": "app", "app": app, "want": want, "did": did}


def is_fact(said: str) -> bool:
    return classify(said)[0] == "fact"


# ------------------------------------------------------------------------------------------------------ the answer
def _title() -> str:
    try:
        from core import users
        return users.title()
    except Exception:  # noqa: BLE001
        return "sir"


def _display(app: str) -> str:
    try:
        from core import targets
        return targets.display(app)
    except Exception:  # noqa: BLE001
        return app.title()


def _windows_of_app(app: str) -> list:
    from tools import apps
    return apps.visible_own_windows(app)


def _running(app: str) -> bool:
    try:
        from tools import apps
        return apps.app_running(app)
    except Exception:  # noqa: BLE001 - unreadable: it is not said to be running
        return False


def answer(values: Dict[str, str]) -> str:
    """The reply, read from the PC just now. When the window list cannot be read it says so; it never guesses. It says "open" only for
    the app's own window, "in front" only when that window is in front, and never "closed": no window seen is said as no window seen."""
    title = _title()
    app = str(values.get("app", ""))
    try:
        windows = _windows_of_app(app)
    except Exception as error:  # noqa: BLE001
        print(f"[pcfacts] could not read the window list: {type(error).__name__}: {error}")
        return f"I can't read the window list just now, {title}, so I won't guess whether {_display(app)} is open."
    name = _display(app)
    closed_asked = values.get("want") == "closed"
    did = bool(values.get("did"))
    if windows:
        front = any(w.get("foreground") for w in windows)
        if closed_asked:
            return f"No, {title}, {name} is still open."
        if did:
            return f"Yes, {title}, {name} is open."          # a state he can see, not an action Jarvis remembers
        return f"Yes, {title}, {name} is open." + (" It's in front." if front else "")
    if _running(app):
        if closed_asked:
            return f"No, {title}, {name} is still running, though I don't see a window."
        if did:
            return f"Yes, {title}, {name} is running, though I don't see a window."
        return f"{name} is running, {title}, but I don't see a window."
    if closed_asked:
        return f"Yes, {title}, I don't see a {name} window any more."       # a clear yes about what is seen; never "closed" (a tray-only app has no window either)
    return f"No, {title}, I don't see a {name} window."
