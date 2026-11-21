"""Language teacher: guided spoken lessons in French, Spanish, Mandarin Chinese, Japanese and Haitian Creole that
remember what he has learned. The course is core/lang_curriculum, the logic core/lang_engine, the wording of requests
core/langintent. Everything works with no model at all; the one local model only adds a livelier conversation turn,
a one-line reason for a repeated slip, and phrases the course does not have.

This module holds the running lesson (a multi-turn mode: one step at a time, in memory per person and channel, gone
after IDLE seconds or a restart; every answer is already saved as an event) and the speech set-up around it: while a
voice lesson is open the recogniser also listens for the lesson's language, and every phrase in that language is
marked so the speaker uses that language's voice, slowly for a beginner, while the English around it keeps Jarvis's
own voice. The functions without @tool return plain data for the Languages page; the @tool skills say the same."""
import contextvars
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from core import lang_curriculum as course
from core import lang_engine as engine
from core import langintent, users
from core.words import plural
from tools import registry

IDLE = 10 * 60                      # a lesson he walked away from is over (what he answered is already saved)
TALK = "talk"                       # the one voice/chat lesson per person; the Languages page's lessons have their own ids
MODEL_SECONDS = 20                  # a model call never holds a lesson turn longer than this
_sessions: dict[tuple, dict] = {}
_last_lang: dict[str, str] = {}     # uid -> the language of his last lesson or question here
_locks: dict[str, threading.RLock] = {}
_channel = contextvars.ContextVar("language_channel", default=TALK)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="language-model")
SUPPORTED = "French, Spanish, Mandarin Chinese, Japanese and Haitian Creole"


class LessonError(Exception):
    """Something he can fix; the message is said to him as it is."""


def _local_model(prompt: str) -> str:
    from tools import study
    return study._local_model(prompt)                      # the one local brain, the way the study tutor reaches it


MODEL = _local_model                                       # tests replace this


def _model(prompt):
    return _pool.submit(contextvars.copy_context().run, MODEL, prompt).result(timeout=MODEL_SECONDS)


def _uid() -> str:
    return users.current()


def _k() -> tuple:
    return (_uid(), _channel.get())


@contextmanager
def on_page(session_id: str):
    """The Languages page's own lesson: it cannot capture, or be captured by, voice and chat."""
    token = _channel.set("page:" + (re.sub(r"[^a-zA-Z0-9]", "", session_id or "")[:32] or "default"))
    try:
        yield
    finally:
        _channel.reset(token)


def new_page_session() -> str:
    for key in [k for k, v in _sessions.items() if time.time() - v["at"] > 6 * IDLE]:
        _sessions.pop(key, None)
    return uuid.uuid4().hex[:16]


def _serial(fn):
    def run(*a, **k):
        with _locks.setdefault(_uid(), threading.RLock()):
            return fn(*a, **k)
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def session() -> dict | None:
    s = _sessions.get(_k())
    if s and (s["done"] or time.time() - s["at"] > (IDLE if _channel.get() == TALK else 6 * IDLE)):
        _sessions.pop(_k(), None)
        if _channel.get() == TALK:
            if not s["done"]:
                engine.finish(s)                           # he walked away: the lesson still counts
            _speech_off()
        return None
    return s


def on_the_table() -> dict | None:
    """What the Brain needs to tell an answer from other talk: the open voice/chat lesson's current step."""
    s = session() if _channel.get() == TALK else None
    st = engine.step(s) if s else None
    if not st:
        return None
    item, lang = s["items"][st["item"]], s["lang"]
    if st["kind"] == "meaning":
        check = lambda said: engine.check_meaning(item, said)
    elif st["kind"] == "talk":
        check = None
    else:
        check = lambda said: engine.check_target(item, said)
    return {"lang": lang, "kind": st["kind"], "check": check, "fresh": time.time() - s["at"] < 180,
            "guess": lambda said: engine.is_meaning(lang, said, s["items"])}


def _code(language: str, need: bool = True) -> str:
    code = langintent.code_of(language)
    if not code and language:
        from tools import languages
        named = languages.named_in(language) & set(course.LANGUAGES)
        code = next(iter(named), "")
    if not code and not str(language or "").strip():
        s = session()
        code = s["lang"] if s else _last_lang.get(_uid(), "")
    if not code and need:
        raise LessonError(f"Which language? I teach {SUPPORTED}.")
    return code


# ---- speech ---------------------------------------------------------------------------------------------------------
def _speech_on(lang: str) -> None:
    """A voice lesson: the recogniser also listens for the language and unsure sentences are that language. An
    interpreter session owns the same switches, so it is left alone."""
    try:
        from tools import languages
        if languages.interpreter is None:
            languages.set_foreign_language(lang)
    except Exception:                                      # noqa: BLE001 - speech set-up never stops a lesson
        pass


def _speech_off() -> None:
    try:
        from audio import language
        from tools import languages
        language.clear_marks()
        if languages.interpreter is None:
            languages.set_foreign_language(None)
    except Exception:                                      # noqa: BLE001
        pass


def text_of(parts: list) -> str:
    """The parts as one line to show and say: target phrases as written, with pinyin or romaji in brackets."""
    out = []
    for n, p in enumerate(parts):
        if isinstance(p, str):
            out.append(p)
            continue
        shown = p.get("show") or p["text"]
        if p.get("roman"):
            shown += f" ({p['roman']})"
        runs_on = n + 1 < len(parts) and isinstance(parts[n + 1], str) and parts[n + 1][:1].islower()      # "Hola means ..."
        out.append(shown if runs_on or shown[-1] in ".!?。？！" else shown + ".")
    return " ".join(out)


def _voice(parts: list) -> None:
    """Tell the speaker which pieces of the line are in the lesson's language (and which to say slowly), and that
    the bracketed pinyin or romaji is for the eye only."""
    try:
        from audio import language
        for p in parts:
            if isinstance(p, dict) and p.get("text"):
                language.mark(p["text"], p["lang"], bool(p.get("slow")))
                if p.get("roman"):
                    language.mark(f"({p['roman']})", "", False)
        language.mark_line(text_of(parts))
    except Exception:                                      # noqa: BLE001
        pass


def _say(parts: list, s: dict | None = None, **more) -> dict:
    _voice(parts)
    out = {"text": text_of(parts), "parts": parts, "done": bool(s is None or s["done"])}
    if s is not None:
        st = engine.step(s)
        out |= {"lang": s["lang"], "phase": engine.phase(s), "phase_name": engine.PHASE_NAMES[engine.phase(s)], "kind": st["kind"] if st else "",
                "step": min(s["i"] + 1, len(s["steps"])), "steps": len(s["steps"]), "right": s["right"], "of": s["of"], "minutes": s["minutes"]}
    return out | more


# ---- the lesson, as data ----------------------------------------------------------------------------------------
@_serial
def lesson_start(language: str = "", minutes: int = 10, from_zero: bool = False) -> dict:
    lang = _code(language)
    old = _sessions.pop(_k(), None)
    if old and not old["done"]:
        engine.finish(old)
    try:
        minutes = int(minutes or 10)
    except (TypeError, ValueError):
        minutes = 10
    s = engine.start(lang, minutes, bool(from_zero))
    s["at"] = time.time()
    _sessions[_k()] = s
    _last_lang[_uid()] = lang
    if _channel.get() == TALK:
        _speech_on(lang)
    return _say(engine.opening(s), s)


def _close(s: dict) -> None:
    _sessions.pop(_k(), None)
    if _channel.get() == TALK:
        _speech_off()


@_serial
def lesson_answer(answer: str) -> dict:
    s = session()
    if not s:
        raise LessonError("No lesson is running. Say, for example, let's practice Spanish for ten minutes.")
    s["at"] = time.time()
    out = engine.answer(s, str(answer or "")[:400], _model)
    if out["done"]:
        _close(s)
    return _say(out["parts"], s, correct=out["correct"], retry=bool(out.get("retry")))


@_serial
def lesson_repeat(slower: bool = False) -> dict:
    s = session()
    if not s:
        raise LessonError("No lesson is running.")
    s["at"] = time.time()
    return _say(engine.repeat(s, bool(slower)), s)


@_serial
def lesson_skip() -> dict:
    s = session()
    if not s:
        raise LessonError("No lesson is running.")
    s["at"] = time.time()
    out = engine.skip(s)
    if out["done"]:
        _close(s)
    return _say(out["parts"], s)


@_serial
def lesson_stop() -> dict:
    s = session()
    if not s:
        return {"text": "No lesson is running.", "parts": ["No lesson is running."], "done": True}
    parts = engine.finish(s)
    _close(s)
    return _say(parts, s)


def _target(lang: str, text: str, roman: str = "", slow: bool = True) -> dict:
    return {"lang": lang, "text": text.replace("...", "").strip(), "roman": roman.replace("...", "").strip(), "slow": slow, "show": text}


@_serial
def say(phrase: str, language: str = "") -> dict:
    """"How do you say X": from the course, else from the local model (then kept as one of his own words)."""
    lang = _code(language)
    _last_lang[_uid()] = lang
    name = course.LANGUAGES[lang]["name"]
    phrase = " ".join(str(phrase or "").split())[:120]
    if not phrase:
        raise LessonError("Say what?")
    s = session()
    if s:
        s["at"] = time.time()
    item = engine.find_phrase(lang, phrase, engine.profile(lang))
    if item:
        return _say([f"\"{item['english']}\" in {name} is", _target(lang, item["text"], item["roman"])], None, found=True)
    got = engine.translate(lang, phrase, False, _model)
    if not got:
        return _say([f"I don't have \"{phrase}\" in my {name} course, and my own brain did not give me a clean answer just now."], None, found=False)
    engine.record({"type": "word", "lang": lang, "text": got["text"], "english": phrase, "roman": got["roman"]})
    return _say([f"\"{phrase}\" in {name} is", _target(lang, got["text"], got["roman"]),
                 "That one is from my own brain, not the checked course. I added it to your words to practise."], None, found=True)


@_serial
def meaning(phrase: str, language: str = "") -> dict:
    lang = _code(language)
    _last_lang[_uid()] = lang
    name = course.LANGUAGES[lang]["name"]
    phrase = " ".join(str(phrase or "").split())[:120]
    if not phrase:
        raise LessonError("Which phrase?")
    s = session()
    if s:
        s["at"] = time.time()
    item = engine.find_meaning(lang, phrase, engine.profile(lang))
    if item:
        return _say([_target(lang, item["text"], item["roman"], False), f"means \"{item['english']}\"."], None, found=True)
    got = engine.translate(lang, phrase, True, _model)
    if not got:
        return _say([f"I don't have \"{phrase}\" in my {name} course, and my own brain did not give me a clean answer just now."], None, found=False)
    return _say([f"\"{phrase}\" means \"{got['english']}\". That is from my own brain, not the checked course."], None, found=True)


def progress(language: str = "") -> dict:
    return engine.progress(_code(language))


def overview() -> dict:
    """Everything the Languages page shows: every language's progress, and the page lesson if one is open."""
    rows = engine.events()
    profiles = engine.fold(rows)
    langs = [engine.progress(code, profiles.get(code) or engine._empty()) for code in course.ORDER]
    s = session()
    return {"languages": langs, "last": _last_lang.get(_uid()) or next((r["lang"] for r in reversed(rows) if r["lang"] in course.LANGUAGES), ""),
            "lesson": _say(s.get("last") or [], s) if s else None, "voices": voices()}


def voices() -> dict:
    """Which voice speaks each lesson language on this machine, said plainly (the page shows it)."""
    try:
        from audio import tts
        return {code: tts.lesson_voice_note(code) for code in course.ORDER}
    except Exception:                                      # noqa: BLE001
        return {}


# ---- the skills -------------------------------------------------------------------------------------------------
def _friendly(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except LessonError as e:
            return str(e)
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def tool(*args, **kwargs):
    def decorator(fn):
        return registry.tool(*args, **kwargs)(_friendly(fn))
    return decorator


@tool("Start a guided spoken language lesson (French, Spanish, Mandarin Chinese, Japanese or Haitian Creole) that runs "
      "for the minutes asked: warm-up review, new phrases, practice, a short conversation, a recap. Use for 'let's "
      "practice Spanish for 20 minutes', 'teach me Japanese from zero', 'continue my French lesson'.",
      {"language": {"type": "string", "description": "e.g. 'Spanish', 'Haitian Creole'"},
       "minutes": {"type": "integer", "description": "Length of the lesson; 10 when he does not say"},
       "from_zero": {"type": "boolean", "description": "True for 'from zero', 'like I have never studied it'"}},
      required=["language"], direct=True)
def language_lesson(language: str, minutes: int = 10, from_zero: bool = False) -> str:
    return lesson_start(language, minutes, from_zero)["text"]


@tool("Take his answer to the running language lesson's question and move to the next step. Pass his words as they "
      "are, in whatever language he said them.", {"answer": {"type": "string"}}, direct=True)
def language_answer(answer: str) -> str:
    return lesson_answer(answer)["text"]


@tool("Say the language lesson's last line again; slower=true for 'slower', 'say it slowly', 'too fast'.",
      {"slower": {"type": "boolean"}}, required=[], direct=True)
def language_repeat(slower: bool = False) -> str:
    return lesson_repeat(slower)["text"]


@tool("Skip the current step of the language lesson.", {}, direct=True)
def language_skip() -> str:
    return lesson_skip()["text"]


@tool("End the running language lesson with a recap.", {}, direct=True)
def language_stop() -> str:
    return lesson_stop()["text"]


@tool("How to say an English phrase in a language he is learning. Use for 'how do you say water in Spanish'.",
      {"phrase": {"type": "string"}, "language": {"type": "string", "description": "Empty during a lesson: the lesson's language"}},
      required=["phrase"], slow=True, direct=True)
def language_say(phrase: str, language: str = "") -> str:
    return say(phrase, language)["text"]


@tool("What a phrase in a language he is learning means in English. Use for 'what does gracias mean'.",
      {"phrase": {"type": "string"}, "language": {"type": "string", "description": "Empty during a lesson: the lesson's language"}},
      required=["phrase"], slow=True, direct=True)
def language_meaning(phrase: str, language: str = "") -> str:
    return meaning(phrase, language)["text"]


@tool("His progress in a language: level, units, what is due for review and what to practise. Use for 'my progress "
      "in Spanish', 'how is my Japanese going', 'what should I practise'.",
      {"language": {"type": "string"}}, required=[], direct=True)
def language_progress(language: str = "") -> str:
    p = progress(language)
    if not p["learned"] and not p["words"]:
        return f"You have not started {p['name']} yet. Say: teach me {p['name']} from zero."
    said = (f"{p['name']}: {p['level'].lower()}, {p['unit']['title']}. {plural(p['learned'], 'phrase')} learned of {p['total']}, "
            f"{len(p['due'])} due for review, {plural(p['lessons'], 'lesson')} and {plural(p['minutes'], 'minute')} so far")
    said += f", {plural(p['streak'], 'day')} in a row." if p["streak"] > 1 else "."
    if p["weak"]:
        weak = p["weak"][:4]
        _voice([_target(p["lang"], w["text"], w.get("roman", ""), False) for w in weak])
        said += " To practise: " + ", ".join(f"{w['text']} ({w['english']})" for w in weak) + "."
    return said
