"""What Jarvis is busy with right now, so "what's the hold up?" gets a real answer without
stopping the task. Tools and the computer-control loop call set(); the voice loop and
the phone app call report() when someone asks for a status update."""
import re
import threading
import time

_lock = threading.Lock()
_now = {"what": "", "since": 0.0, "step": "", "task_start": 0.0}

STATUS_QUESTION = re.compile(
    r"\b(what'?s the hold ?up|what is the hold ?up|what'?s taking (so )?long|what is taking (so )?long|"
    r"how long|status|progress|update me|how'?s it going|how is it going|how'?s that going|are you done|"
    r"you done|are you finished|what are you doing|what'?s going on|where are you at|almost done|"
    r"ki sa k ap fèt|kisa k ap fèt|sa k pase|kote w rive|ou fini|w fini|eske w fini|kisa w ap fè|"
    r"où en es[- ]tu|ça avance|tu as fini|t'as fini|qu'est[- ]ce qui se passe|c'est long|"
    r"what'?s up with that|still working)\b", re.I)


def is_status_question(text: str) -> bool:
    t = re.sub(r"^\[[^\]]*\]\s*", "", text.strip().lower())
    return bool(STATUS_QUESTION.search(t)) and len(t.split()) <= 12


def start(what: str) -> None:
    with _lock:
        _now.update(what=what, since=time.monotonic(), step="")
        if not _now["task_start"]:
            _now["task_start"] = time.monotonic()


def step(text: str) -> None:
    with _lock:
        _now["step"] = text


def done() -> None:
    with _lock:
        _now.update(what="", since=0.0, step="", task_start=0.0)


def _secs(s: float) -> str:
    s = int(s)
    return f"{s} seconds" if s < 90 else f"{s // 60} minute{'s' if s >= 120 else ''}" + (f" {s % 60} seconds" if s < 600 and s % 60 else "")


def report(lang: str = "en") -> str:
    with _lock:
        what, since, stp, t0 = _now["what"], _now["since"], _now["step"], _now["task_start"]
    if not what:
        return {"ht": "M ap reflechi sou sa w mande a, m prèske fini.",
                "fr": "Je réfléchis à votre demande, j'ai presque fini."}.get(lang, "I'm thinking it through; nearly there.")
    total = _secs(time.monotonic() - (t0 or since))
    detail = f" Right now: {stp}." if stp and what not in stp else ""   # not "I'm closing X... Right now: step 1 of 1, closing X"
    if lang == "ht":
        return f"M ap {what}, sa fè {total}.{detail} M ap kontinye."
    if lang == "fr":
        return f"Je suis en train de {what}, depuis {total}.{detail} Je continue."
    return f"I'm {what}, {total} in so far.{detail} Still going."
