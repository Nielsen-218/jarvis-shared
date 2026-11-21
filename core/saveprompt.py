"""Closing something that asks to save (owner 2026-11-07):

  "close it"                                  -> it asks to save: Jarvis asks "Save it before I close it?" -> yes / no
  "save it and close it" / "save it, then close it" / "close it and save it"   -> Save, then closed
  "don't save it, just close it" / "close it without saving"                   -> Don't save, then closed

The app's OWN save prompt is answered by pressing its own button (UI Automation, by name: Save / Don't save), so nothing
is guessed: without such a prompt there is nothing to answer, and an untitled document's "Save as" window is left for the
owner to name. Games are never asked (core.pcactions ends a game that ignores the close).
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

_SAVE = ("save", "save changes", "yes")
_DONT = ("don't save", "dont save", "do not save", "no", "discard", "discard changes", "close without saving")
_PENDING: Dict[str, dict] = {}
PENDING_S = 300.0

_CLOSE = r"(?:close|quit|exit|shut)"
_NOSAVE = re.compile(r"\b(?:don'?t|do not|dont|without|no)\s+(?:sav(?:e|ing)|keep)\b|\bdiscard\b", re.I)
_WITHSAVE = re.compile(r"\bsave\b", re.I)
_TARGET = re.compile(rf"\b{_CLOSE}\s+(?:it|this|that|the window|this window|(?P<app>[a-z0-9][\w .+-]{{1,40}}?))(?=\s*(?:$|[,.!?]|\band\b|\bthen\b|\bwithout\b|\bbut\b))",
                     re.I)


def command(said: str) -> Optional[tuple]:
    """(save: bool, app or '') for a close that says what to do about saving; None for anything else."""
    text = re.sub(r"^\W*(?:hey\s+)?(?:jarvis|jervis)\W*", "", said or "", flags=re.I).strip()
    m = _TARGET.search(text)
    if not m or not (_NOSAVE.search(text) or _WITHSAVE.search(text)):
        return _understood(text)
    app = (m.group("app") or "").strip()
    if app.casefold() in ("it", "this", "that", "out"):
        app = ""
    return (not _NOSAVE.search(text), app)


def _name(control) -> str:
    return re.sub(r"\s+", " ", (control.element_info.name or "").strip()).casefold().replace("&", "").replace("\u2019", "'")


_ANSWERS = {"save": "save the work / keep the changes, then close", "dont_save": "close without saving / throw the changes away"}
# Worth asking the model only when the sentence is about closing AND about keeping or losing the work.
_CLOSING = re.compile(r"\b(?:close|shut|quit|exit|kill|get rid of|done with|finish(?:ed)? with|end)\b", re.I)
_KEEPING = re.compile(r"\b(?:sav(?:e|ed|ing)|keep|lose|losing|toss|trash|discard|throw (?:it )?away)\b", re.I)


def _understood(text: str):
    """"Shut paint down but keep my drawing", "close it, I don't need it saved": the local model reads what they mean."""
    if not (_CLOSING.search(text) and _KEEPING.search(text)):
        return None
    from core import meaning
    pick = meaning.choose(text, {"close_save": "close it and save the work first", "close_dont_save": "close it without saving"},
                          "The person may be asking the assistant to close an app on their PC.", max_words=20)
    if pick is None:
        return None
    named = re.search(r"\b(?:close|shut|quit|exit|kill|get rid of|done with)\s+(?:down\s+)?(?!it\b|this\b|that\b|down\b)"
                      r"([a-z0-9][\w+-]*(?:\s+[a-z0-9][\w+-]*)?)", text, re.I)
    app = ""
    if named:
        from tools import apps
        words = named.group(1).split()
        for n in (2, 1):
            if len(words) >= n and apps.resolve_app(" ".join(words[:n])):
                app = " ".join(words[:n])
                break
    return (pick == "close_save", app)


def _windows_of(pids) -> list:
    from pywinauto import Desktop
    from jarvis_pc.uiact import uia
    uia.ensure_mta()
    out = []
    for w in Desktop(backend="uia").windows():
        try:
            if w.process_id() in pids:
                out.append(w)
        except Exception:  # noqa: BLE001
            continue
    return out


def buttons(app: str = "", hwnd: int = 0) -> Dict[str, object]:
    """The Save / Don't save buttons of the save prompt the app is showing ({} when it shows none)."""
    try:
        from tools import apps, pcops
        wins = apps.visible_windows_for_app(app) if app else [w for w in pcops.windows() if int(w.get("hwnd") or 0) == int(hwnd)]
        pids = {w.get("pid") for w in wins if w.get("pid")}
        if not pids:
            return {}
        # The prompt's Save is the one NEXT TO its Don't save: Paint's toolbar has a Save button too (live 2026-11-07).
        for win in _windows_of(pids):
            for dont in win.descendants(control_type="Button"):
                if _name(dont) not in _DONT:
                    continue
                save = next((b for b in dont.parent().children() if b.element_info.control_type == "Button" and _name(b) in _SAVE), None)
                if save is not None:
                    return {"save": save, "dont": dont}
        return {}
    except Exception:  # noqa: BLE001  (unreadable: treated as no prompt)
        return {}


def remember(uid: str, app: str, hwnd: int = 0) -> str:
    """The close is waiting on the app's save prompt: ask the owner, and keep what to answer it for their yes/no."""
    _PENDING[uid] = {"app": app, "hwnd": hwnd, "t": time.time()}
    name = (app or "it").title() if app else "It"
    return f"{name} has unsaved changes. Save it before I close it?"


def pending(uid: str) -> Optional[dict]:
    p = _PENDING.get(uid)
    return p if p and time.time() - p["t"] < PENDING_S else None


def withdraw(uid: str, before: Optional[dict] = None) -> bool:
    """'Save it before I close it?' waiting now, if it is not `before` (pending() taken before the turn), was never said
    (he cut in): no yes answers it."""
    p = _PENDING.get(uid)
    if p is not None and p is not before:
        _PENDING.pop(uid, None)
        return True
    return False


_YES = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|save(?: it)?(?: please)?|yes,? save(?: it)?)\W*$", re.I)
_NO = re.compile(r"^\W*(?:no|nope|nah|don'?t(?: save)?(?: it)?|do not save|don'?t save it,? just close it|just close it|discard(?: it)?)\W*$", re.I)


def _asked_after(uid: str, p: dict) -> bool:
    """The save prompt `p` was asked after the words being answered now came in (core.turnstate.heard_at: a spoken yes/no
    that waited behind the companion's typed close on the PC): those words were never its answer (review 2026-11-11)."""
    try:
        from core import turnstate
        return turnstate.asked_after_heard(uid, f"save:{p.get('t')}")
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[saveprompt] could not tell when the save question was asked: {type(error).__name__}: {error}")
        return False


def reply(uid: str, said: str) -> Optional[str]:
    """Their yes / no to "Save it before I close it?"; None when this is not that answer, or when it was asked after these
    words came in (it stays waiting for his answer)."""
    p = pending(uid)
    if not p or _asked_after(uid, p):
        return None
    text = re.sub(r"^\W*(?:hey\s+)?(?:jarvis|jervis)\W*", "", said or "", flags=re.I).strip()
    choice = True if _YES.match(text) else False if _NO.match(text) else None
    if choice is None:
        cmd = command(text)
        choice = cmd[0] if cmd else None
    if choice is None:                     # their own words: "keep it", "nah toss it", "I want to keep my drawing"
        from core import meaning
        pick = meaning.choose(text, _ANSWERS, f"The assistant asked: '{(p['app'] or 'It').title()} has unsaved changes. "
                                              "Save it before I close it?'", max_words=15)
        choice = {"save": True, "dont_save": False}.get(pick)
    if choice is None:
        return None
    _PENDING.pop(uid, None)
    return answer(p["app"], choice, p.get("hwnd", 0))


def answer(app: str, save: bool, hwnd: int = 0, *, wait_s: float = 8.0) -> str:
    """Press the prompt's own Save or Don't save, then check the app really closed."""
    from jarvis_pc.guardrails.stepfence import BackgroundUnavailable
    from tools import apps
    name = (app or "the app").title() if app else "It"
    found = buttons(app, hwnd)
    if not found:
        return f"{name} isn't asking to save anymore."
    try:
        btn = found["save" if save else "dont"]
        try:
            btn.invoke()
        except Exception:  # noqa: BLE001
            # click_input is the real mouse: never in a mission (2026-11-10, Mark VI); the refusal is not swallowed below.
            from jarvis_pc.guardrails.stepfence import require_foreground
            require_foreground("press the save prompt with the mouse")
            btn.click_input()
    except BackgroundUnavailable:
        raise
    except Exception as error:  # noqa: BLE001
        return f"I couldn't press {'Save' if save else 'Don’t save'} ({type(error).__name__}); the prompt is still open."
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        still = apps.visible_windows_for_app(app) if app else []
        if app and not still:
            return f"Saved and closed {name}." if save else f"Closed {name} without saving."
        if save and _save_as_open(app):
            return f"{name} has no file name yet, so I left the Save as window open for you to name it."
        time.sleep(0.25)
    if not app:
        return "Saved and closed it." if save else "Closed it without saving."
    return f"I pressed {'Save' if save else 'Don’t save'}, but {name} is still open."


def _save_as_open(app: str) -> bool:
    try:
        from tools import apps
        return any(re.search(r"\bsave as\b", w.get("title", ""), re.I) for w in apps.visible_windows_for_app(app))
    except Exception:  # noqa: BLE001
        return False


def close_with(uid: str, save: bool, app: str = "") -> str:
    """"Save it and close it" / "don't save it, just close it": close (the named app, else the app in front) and answer its
    save prompt the way the owner already said."""
    from core import pcactions
    from tools import apps, pcops
    if app:
        canonical = apps.resolve_app(app)
        if not canonical:
            return f"I can't find an app called {app}."
    else:
        front = next((w for w in pcops.windows() if w.get("foreground")), None)
        canonical = apps.resolve_app(front.get("process", "").removesuffix(".exe")) if front else None
        if not canonical:
            canonical = apps.resolve_app(front.get("title", "")) if front else None
        if not canonical:
            return "I can't tell which app is in front to close."
    out = pcactions._close_catalog_app(canonical, save=save)
    return out["detail"]
