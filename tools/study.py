"""Study companion: his own course material (and a built-in IT fundamentals outline) taught back to him. Quizzes
grounded in the text with the page cited, answers graded, explanations on a six-rung ladder (college down to a one-year-old) with a whiteboard, "another way", flashcards with
spaced repetition, progress, and a plan up to an exam date.

The store is core/study_store, the logic core/study_engine. This module holds the running session (a quiz or a card
review is a multi-turn mode: one question at a time, in memory per person, gone after IDLE seconds or a restart; what
he answered is already saved as events) and the wording. The functions without @tool return plain data and are what
the Study page's API calls; the @tool skills say the same things out loud."""
import contextvars
import os
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date

from core import storage, study_board as board, study_check as check, study_engine as engine, study_ladder as ladder, study_store as store, users
from core.words import plural
from tools import registry

IDLE = 10 * 60                      # a session he walked away from is over
TALK = "talk"                       # the one voice/chat session per person; the Study page's quizzes have their own ids
MODEL_SECONDS = 45                  # one model call never holds a turn longer than this
MAX_TEXT = 2 * 1024 * 1024          # pasted notes
_sessions: dict[tuple, dict] = {}   # (uid, channel) -> the running quiz or card review
_explained: dict[tuple, dict] = {}  # (uid, channel) -> the last explanation (topic, passages, rung, analogies used)
_notice: dict[str, dict] = {}       # uid -> the whiteboard of the explanation just given, for the chat reply (take_notice)
_boards: dict[str, dict] = {}       # uid -> the last explanation given by voice or chat, with its whiteboard (the Study page shows it)
at_pc = contextvars.ContextVar("study_at_pc", default=True)   # False while a turn from the phone runs: nothing opens on the PC screen
_unavailable: dict[str, str] = {}   # uid -> the general topic the model could not teach just now (see take_unavailable)
_follow: dict[str, int] = {}        # uid -> turns left in which a spoken follow-up ("another way") is about study
_follow_at: dict[str, float] = {}      # uid -> when the last spoken study reply was given
FOLLOW_SECONDS = 6 * 60
_locks: dict[str, threading.RLock] = {}
_channel = contextvars.ContextVar("study_channel", default=TALK)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="study-model")
_rng = random.Random()


def _local_model(prompt: str) -> str:
    """The one local brain, the way every other caller reaches it: on the PC directly, from the laptop over the
    authenticated PC link. Any failure raises, and the engine then uses the material's own words."""
    from core import intelligence, pcbridge
    body = {"role": "fast", "tools": [], "messages": [{"role": "user", "content": prompt}]}
    if not pcbridge.local_pc() and os.getenv("JARVIS_PC_URL"):
        reply = pcbridge.json_request("/api/pc/intelligence", "POST", body)
    else:
        reply = intelligence.complete_local(body)
    return reply["choices"][0]["message"].get("content") or ""


MODEL = _local_model                # tests replace this


def _model(prompt):
    """MODEL with a deadline: a slow or hung model raises here (the engine then uses the material's own words)
    instead of holding the turn. The call keeps the person it is for (contextvars)."""
    return _pool.submit(contextvars.copy_context().run, MODEL, prompt).result(timeout=MODEL_SECONDS)


def _k() -> tuple:
    return (_uid(), _channel.get())


@contextmanager
def on_page(session_id: str):
    """The Study page's own session: nothing done inside can capture, or be captured by, voice and chat."""
    token = _channel.set("page:" + (re.sub(r"[^a-zA-Z0-9]", "", session_id or "")[:32] or "default"))
    try:
        yield
    finally:
        _channel.reset(token)


def new_page_session() -> str:
    for key in [k for k, v in _sessions.items() if time.time() - v["at"] > 6 * IDLE]:
        _sessions.pop(key, None)                          # abandoned page quizzes do not pile up
    for key in [k for k, v in _explained.items() if time.time() - v.get("at", 0) > 6 * IDLE]:
        _explained.pop(key, None)
    return uuid.uuid4().hex[:16]


def _serial(fn):
    """One at a time per person: a double submit cannot grade a question twice or skip one."""
    def run(*a, **k):
        with _locks.setdefault(_uid(), threading.RLock()):
            return fn(*a, **k)
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def follow_up_age() -> int:
    """Turns since the last spoken study reply: 1 = this is the very next turn, 2 = the one after, 3 = the third, 0 = no
    longer (three turns or FOLLOW_SECONDS, whichever is first). An explanation he is still working through survives a
    side remark; "simpler" a quarter of an hour later is ordinary talk."""
    left = _follow.get(_uid(), 0)
    if left and time.time() - _follow_at.get(_uid(), 0) > FOLLOW_SECONDS:
        _follow.pop(_uid(), None)
        return 0
    return {3: 1, 2: 2, 1: 3}.get(left, 0)


def take_unavailable() -> str:
    """The general-knowledge topic the last explain could not put together (no model reached), once, else "". The Brain then
    leaves the line to the ordinary chat path, whose tiers (local, Gemini, Claude) can still answer it."""
    return _unavailable.pop(_uid(), "")


def note_turn(study_reply: bool) -> None:
    """Called once per voice/chat turn by the Brain: a study reply opens three turns of follow-up, any other turn uses one."""
    if study_reply:
        _follow[_uid()] = 3
        _follow_at[_uid()] = time.time()
    elif _follow.get(_uid()):
        _follow[_uid()] -= 1


def _uid() -> str:
    return users.current()


def session() -> dict | None:
    s = _sessions.get(_k())
    if s and time.time() - s["at"] > (IDLE if _channel.get() == TALK else 6 * IDLE):
        _sessions.pop(_k(), None)
        return None
    return s


def awaiting_answer() -> bool:
    """A voice/chat question or card is on the table. (A quiz on the Study page never is: it lives on the page.)"""
    return _channel.get() == TALK and session() is not None


def on_the_table() -> dict | None:
    """What the Brain needs to tell an answer from other talk: the open voice/chat question (or card) in the shape
    core.studyintent.looks_like_answer reads, with `fresh` (asked in the last three minutes)."""
    s = session() if _channel.get() == TALK else None
    if not s:
        return None
    if s["mode"] == "cards":
        card = s["queue"][s["i"]]
        return {"stem": card["front"], "answer": card["back"], "quote": "", "fresh": time.time() - s["at"] < 180}
    q = s["q"]
    return {"options": q.get("options"), "stem": q["stem"], "answer": q["answer"], "quote": q.get("quote", ""),
            "fresh": time.time() - s["at"] < 180, "picks": lambda said: engine.choice(q, said) is not None}


def _course(name: str) -> dict:
    c = store.find_course(name)
    if not c:
        known = ", ".join(x["title"] for x in store.courses())
        raise store.StudyError(f"I have no course called {name}. Your courses: {known}.")
    return c


# ---- material ---------------------------------------------------------------------------------------------------
def overview() -> dict:
    """Everything the Study page shows at once: courses with materials and progress, cards due, the running session."""
    st = engine.state()
    out = []
    for c in store.courses():
        prog = engine.progress(c["id"], st)
        out.append(dict(c, progress=prog, chapter_names=[x["chapter"] for x in prog["chapters"]]))
    return {"courses": out, "due": len(engine.due_cards(st)), "pdf": store.pdf_ready()}


# ---- quiz -------------------------------------------------------------------------------------------------------
def _public(q: dict, s: dict) -> dict:
    return {"id": q["id"], "kind": q["kind"], "stem": q["stem"], "options": q.get("options"), "chapter": q["chapter"],
            "n": s["i"] + 1, "of": len(s["passages"])}


def _pick(course_id: str, chapter: str | None, count: int) -> list[dict]:
    """Passages for a quiz: the ones he last got wrong first, then ones never asked, then the rest; shuffled inside
    each group so two quizzes on a chapter differ, and mixed across chapters when no chapter is named."""
    pool = [p for p in store.passages(course_id) if (not chapter or p["chapter"] == chapter) and len(p["text"]) >= 60]
    last = {}
    for row in engine.events():
        if row["type"] == "answer" and row.get("passage_id"):
            last[row["passage_id"]] = bool(row.get("correct"))
    _rng.shuffle(pool)
    pool.sort(key=lambda p: {False: 0, None: 1, True: 2}[last.get(p["id"])])
    return _stretch(pool, count)


def _stretch(pool: list[dict], count: int) -> list[dict]:
    """Up to `count` passages; when there are fewer, a passage that defines several terms is asked again about its
    next term (`variant`), so a one-page chapter still gives more than one question."""
    picked, variant = pool[:count], 1
    while len(picked) < count:
        more = [dict(p, variant=variant) for p in pool if len(engine.definitions(p)) > variant]
        if not more:
            break
        picked += more[:count - len(picked)]
        variant += 1
    return picked


def _next_question(s: dict) -> dict:
    passage = s["passages"][s["i"]]
    kind = s["kinds"][s["i"] % len(s["kinds"])]
    siblings = [p for p in s["siblings"] if p["id"] != passage["id"]]
    s["q"], s["tries"], s["at"] = engine.make_question(passage, kind, s["course"], _model, siblings, _rng), 0, time.time()
    return s["q"]


def quiz_start(course: str = "", chapter: str = "", count: int = 5, kind: str = "mixed") -> dict:
    c = _course(course)
    name = store.find_chapter(c["id"], chapter) if chapter.strip() else None
    try:
        count = max(1, min(int(count or 5), 30))
    except (TypeError, ValueError):
        raise store.StudyError("The number of questions must be a number.") from None
    if chapter.strip() and not name:                      # not a chapter's name: a topic ("quiz me on subnetting")
        picked = _stretch([p for p in store.search(chapter, c["id"], k=count) if "course" in p], count)
        name = chapter.strip() if picked else None
        if not picked:
            names = "; ".join(x["chapter"] for x in store.chapters(c["id"])[:12])
            raise store.StudyError(f"I can't find that chapter or topic in {c['title']}. It has: {names}.")
    else:
        picked = _pick(c["id"], name, count)
    if not picked:
        raise store.StudyError(f"{c['title']} has no material yet. Add notes or a PDF first.")
    kinds = {"mixed": ["mcq", "short", "mcq", "scenario"], "exam": ["mcq"]}.get(kind, [kind if kind in engine.KINDS else "mcq"])
    s = {"mode": "quiz", "course": c["id"], "course_title": c["title"], "chapter": name or "", "passages": picked,
         "siblings": store.passages(c["id"]), "kinds": kinds, "i": 0, "right": 0, "results": [], "at": time.time()}
    _sessions[_k()] = s
    q = _next_question(s)
    return {"course": c["id"], "course_title": c["title"], "chapter": s["chapter"], "question": _public(q, s)}


_SKIPPED = re.compile(r"^\W*(?:skip|pass|never ?mind|skip (?:it|this|that)(?: one| question)?|next question)\W*$", re.I)


@_serial
def quiz_answer(answer: str, question_id: str = "") -> dict:
    """Grade his answer to the question on the table. -> {result, retry, question (the next one) | summary}.
    question_id (the page sends it): an answer to a question that is no longer the current one is refused, so a
    double tap cannot answer the next question unseen. "skip" moves on without recording anything."""
    s = session()
    if not s or s["mode"] != "quiz":
        raise store.StudyError("There is no quiz running. Say 'quiz me' to start one.")
    q = s["q"]
    if question_id and question_id != q["id"]:
        raise store.StudyError("That question was already answered.")
    s["at"] = time.time()
    if _SKIPPED.match(answer or ""):
        skipped = {"correct": False, "score": 0.0, "feedback": f"Skipped. The answer was: {q['answer']}", "answer": q["answer"],
                   "quote": q.get("quote", ""), "cite": q["cite"], "missing": [], "understood": True, "why": "", "skipped": True}
        return _advance(s, q, skipped, record=False)
    if re.fullmatch(r"\W*(?:a |give me a |can i (?:have|get) a )?hint(?: please)?\W*", answer or "", re.I):
        return {"result": {"feedback": f"Hint: it is covered in {q['cite']}.", "understood": True, "hint": True},
                "retry": True, "question": _public(q, s)}
    result = engine.grade(q, answer, _model)
    if not result["understood"]:
        return {"result": result, "retry": True, "question": _public(q, s)}
    if q["kind"] != "mcq" and not result["correct"] and s["tries"] == 0 and not engine.gave_up(answer):
        s["tries"] = 1                                    # one more go at a written answer, with a pointer, before the reveal
        hint = dict(result, answer="", quote="", why="", feedback=f"{result['feedback']} Have one more go. Hint: it is in {q['cite']}.")
        return {"result": hint, "retry": True, "question": _public(q, s)}
    return _advance(s, q, result, record=True)


def _advance(s: dict, q: dict, result: dict, record: bool) -> dict:
    if record:
        engine.record({"type": "answer", "key": q["key"], "course": q["course"], "chapter": q["chapter"], "concept": q["concept"],
                       "kind": q["kind"], "correct": bool(result["correct"] and s["tries"] == 0), "passage_id": q["passage_id"]})
    # "explain that" / "I don't understand" right after an answer is about the question just answered
    _explained[_k()] = {"topic": q["concept"], "passages": [s["passages"][s["i"]]], "level": "", "at": time.time(),
                          "turns": 0, "analogies": [], "text": ""}
    if not result.get("skipped"):
        s["right"] += result["correct"]
        s["results"].append({"concept": q["concept"], "correct": result["correct"], "cite": q["cite"]})
    s["i"] += 1
    out = {"result": result, "retry": False}
    if s["i"] < len(s["passages"]):
        out["question"] = _public(_next_question(s), s)
    else:
        _sessions.pop(_k(), None)
        missed = [r for r in s["results"] if not r["correct"]]
        out["summary"] = {"right": s["right"], "of": len(s["results"]), "missed": missed, "course": s["course"]}
    return out


def stop() -> dict:
    s = _sessions.pop(_k(), None)
    if not s:
        return {"stopped": False}
    return {"stopped": True, "mode": s["mode"], "right": s.get("right", 0), "of": len(s.get("results", []))}


# ---- explanations: the ladder and the whiteboard --------------------------------------------------------------------
def _material(topic: str, course_id: str | None = None) -> list[dict]:
    """The passages of his material that are ABOUT `topic` (every word of a short topic is in the best passage), else [].
    A passage that merely shares one word with it ("memory management" -> a page on virtual memory) is not about it."""
    words = set(store.tokens(topic))
    if not words:
        return []
    hits = store.search(topic, course_id, k=3)
    if not hits:
        return []
    top = set(store.tokens(f"{hits[0]['text']} {hits[0]['section']} {hits[0]['chapter']}"))
    return hits if len(words & top) / len(words) >= (1.0 if len(words) <= 4 else 0.6) else []


def _general(topic: str) -> dict:
    """The stand-in passage for a topic his library has nothing on: the engine teaches it from the model's own knowledge."""
    return {"id": "general:" + store.slug(topic), "chapter": "", "section": "", "page": None, "material": "General knowledge",
            "text": "", "general": True}


def _sig(passages) -> tuple:
    """Which material an explanation was built from: held text is replayed only for the same course, chapter and passages."""
    return tuple((p.get("course") or "", p.get("chapter") or "", p.get("section") or "", p.get("id") or "",
                  hash(p.get("text") or "")) for p in passages or [])


def explain(topic: str = "", level: str = "", course: str = "", another: bool = False, fresh: bool = False,
            reuse: bool = False) -> dict:
    """Explain a topic from his material at one rung of the ladder (core/study_ladder), with its whiteboard scene.
    No topic ("explain this"): the question he just answered or the last topic, else the question on the table. A topic
    his material has nothing on is taught from the model's general knowledge (`general`, and `note` says so the first time).
    level: a rung (college, high, middle, elementary, prek, one), or "simpler" / "deeper": ONE rung down or up from
    where his last explanation of this concept was; empty: that same rung (college the first time).
    fresh: he asked to be taught a topic by name (voice/chat), so with no level it starts at the top rung again, not
    wherever an earlier session left it; the Study page leaves it off and picks up the rung he was on. The rung is
    remembered per concept and per person (a "rung" event in his study log, so it follows him between machines).
    another=True with no level: the same rung again, with a comparison not used for it before. reuse: the explanation he
    has, unchanged (the whiteboard of what was just said). One step past either end of the ladder gives back what he has
    and says so (`edge`), instead of the same text again."""
    last, s = _explained.get(_k()) or {}, session()
    recent = bool(last.get("topic")) and time.time() - last["at"] < IDLE
    named = " ".join((topic or "").split())
    topic = named
    if topic and not (another and recent and last["topic"].lower() == topic.lower()):
        c = store.find_course(course) if course else None
        passages = _material(topic, c["id"] if c else None) or [_general(topic)]
    elif recent:
        topic, passages = last["topic"], last["passages"]
    elif s and s["mode"] == "quiz":
        topic, passages = s["q"]["concept"], [s["passages"][s["i"]]]
    else:
        raise store.StudyError("Explain what? Name the topic, for example: explain DNS like I'm in middle school.")
    general = bool(passages[0].get("general"))
    concept = passages[0].get("concept") or ""
    key = store.slug(concept or topic)
    same = recent and (last.get("key") == key or last["topic"].lower() == topic.lower())
    want = ladder.norm(level)
    remembered = "" if general else (engine.state()["rungs"].get(key) or "")
    restart = bool(fresh and named and not want and not another)
    stood = "" if restart else ((last.get("level") if same else "") or remembered)
    rung = ladder.move(stood or ladder.START, want)
    edge = ("bottom" if want == "simpler" and stood == ladder.RUNGS[-1]
            else "top" if want == "deeper" and (stood or ladder.START) == ladder.RUNGS[0] else "")
    held = last.get("out") if same else None
    if held and (edge or (reuse and not want and not another)):
        out = dict(held, edge=edge, asked=want, repeated=bool(edge), note="")      # what he has, not the same text again
        if _channel.get() == TALK:
            _boards[_uid()] = dict(out, at=time.time())
            _notice[_uid()] = _board_notice(out)
        return out
    used = list(last["analogies"]) if same else []
    turn = last["turns"] if same else 0
    previous = {"level": last.get("level"), "text": last["text"]} if same and last.get("text") else None
    again = bool(another and not want and same)
    # Comparisons he did not follow are banned only when he asks for another way at the SAME rung. Stepping down the ladder is
    # not "I did not understand that picture": the ladder's own rule (same idea, one step simpler) keeps it from repeating, and a
    # ban that grew with every step left the bottom rungs nothing to compare to.
    held_rungs = {} if restart or not same or last.get("sig") != _sig(passages) else dict(last.get("rungs") or {})
    going_up = bool(stood) and stood in ladder.RUNGS and ladder.RUNGS.index(rung) < ladder.RUNGS.index(stood)
    if going_up and not again and held_rungs.get(rung):
        # He is stepping back UP to a rung he already heard in this lesson: say that text again, not a new draft or a template.
        out = dict(held_rungs[rung], edge="", asked=want, repeated=False, note="")
    else:
        out = engine.explain(topic, passages, rung, _model, avoid=used if again else [], turn=turn, previous=previous, another=again)
    if general and not out["text"]:
        _unavailable[_uid()] = topic
        raise store.StudyError(f"I could not put {topic} together just now. Ask me again in a moment.")
    if rung != remembered and not general:
        engine.record({"type": "rung", "key": key, "rung": rung, "concept": concept or topic})
    out = dict(out, topic=topic, key=key, concept=concept or topic, edge=edge, asked=want, general=general,
               note="" if turn or not general else NOT_FROM_COURSE,
               course=passages[0].get("course") or "", chapter=passages[0].get("chapter") or "",
               ladder=[{"id": r, "label": ladder.LABELS[r]} for r in ladder.RUNGS])
    _explained[_k()] = {"topic": topic, "key": key, "passages": passages, "level": rung, "at": time.time(), "turns": turn + 1,
                        "analogies": used + ([out["analogy"]] if out["analogy"] else []), "text": out["text"], "out": out,
                        "rungs": {**held_rungs, rung: out}, "sig": _sig(passages)}
    if _channel.get() == TALK:
        _boards[_uid()] = dict(out, at=time.time())           # what he was just told by voice or chat: the Study page can draw it
        _notice[_uid()] = _board_notice(out)
    return out


NOT_FROM_COURSE = "That is not from your course material, so this is from general knowledge."


def _board_notice(out: dict) -> dict:
    """What the phone and the desktop need to show the whiteboard of the explanation just given (see take_notice)."""
    return {"topic": out.get("concept") or out.get("topic") or "", "rung": out.get("level") or "", "label": out.get("label") or "",
            "page": "study.html?board=last", "at": time.time(), "steps": len((out.get("board") or {}).get("steps") or [])}


def take_notice() -> dict | None:
    """The whiteboard notice of the last voice/chat explanation, once: the app server puts it on the chat reply so the
    phone shows which board to open. None when this turn gave no explanation."""
    return _notice.pop(_uid(), None)


def last_board() -> dict | None:
    """The explanation he last got by voice or chat, with its whiteboard, while it is still recent."""
    found = _boards.get(_uid())
    return found if found and time.time() - found["at"] < 6 * IDLE else None


def concepts(course: str = "") -> dict:
    """What the Study page's Teach view lists: per chapter the concepts that can be explained, each with the rung he
    is on. Built-in course: its outline (hand = hand-written rungs and a hand-made board). His own material: the
    terms it defines."""
    c = _course(course)
    rungs = engine.state()["rungs"]
    chapters = []
    if c.get("builtin"):
        for chapter, items in store.study_seed.OUTLINE:
            chapters.append({"chapter": chapter, "concepts": [
                {"name": term, "rung": rungs.get(store.slug(term), ""), "hand": term in ladder.HAND} for term, _ in items]})
    else:
        by_chapter: dict[str, list] = {}
        for p in store.passages(c["id"]):
            terms = by_chapter.setdefault(p["chapter"], [])
            for term, _ in engine.definitions(p):
                if term.lower() not in (t.lower() for t in terms) and len(terms) < 16:
                    terms.append(term)
        chapters = [{"chapter": ch, "concepts": [{"name": t, "rung": rungs.get(store.slug(t), ""), "hand": False} for t in terms]}
                    for ch, terms in by_chapter.items()]
    return {"course": c["id"], "course_title": c["title"], "builtin": bool(c.get("builtin")), "chapters": chapters,
            "ladder": [{"id": r, "label": ladder.LABELS[r]} for r in ladder.RUNGS]}


def _open_board() -> bool:
    """Open the Study page at the board just made, in this PC's browser and already signed in, the way
    tools.interface.open_jarvis_page opens the other pages. Only on the PC itself, and never for a turn that came from
    the phone. -> opened?"""
    from core import pcbridge
    if not (pcbridge.local_pc() and at_pc.get()):
        return False
    try:
        import webbrowser

        from core.appserver import pc_page_url
        from tools.software import is_admin, launch_as_user
        url = pc_page_url(_uid(), "study.html?board=last")
        if is_admin():
            launch_as_user(url)                           # the browser opens with normal rights
        else:
            webbrowser.open(url)
        return True
    except Exception:                                     # noqa: BLE001 - the explanation was still given; the page is one tap away
        return False


OPEN_BOARD = _open_board            # tests replace this


# ---- flashcards -------------------------------------------------------------------------------------------------
def cards_make(course: str = "", chapter: str = "", count: int = 8) -> dict:
    c = _course(course)
    name = store.find_chapter(c["id"], chapter) if chapter.strip() else None
    if chapter.strip() and not name:
        raise store.StudyError(f"I can't find that chapter in {c['title']}.")
    have = engine.state()["cards"]
    pool = [p for p in store.passages(c["id"]) if not name or p["chapter"] == name]
    if not pool:
        raise store.StudyError(f"{c['title']} has no material yet. Add notes or a PDF first.")
    try:
        count = max(1, min(int(count or 8), 40))
    except (TypeError, ValueError):
        raise store.StudyError("The number of cards must be a number.") from None
    made = [card for card in engine.make_cards(pool, c["id"], count + len(have), _model) if card["id"] not in have][:count]
    for card in made:
        engine.record(dict(card, type="card"))
    return {"course": c["id"], "course_title": c["title"], "chapter": name or "", "made": made}


def cards_due(course: str = "") -> list[dict]:
    c = store.find_course(course) if course else None
    return engine.due_cards(engine.state(), c["id"] if c else None)


@_serial
def card_review(card_id: str, rating: str) -> dict:
    """Record one review (again, hard, good, easy). -> the card with its next due date."""
    if rating not in engine.QUALITY:
        raise store.StudyError("Rate the card again, hard, good or easy.")
    if card_id not in engine.state()["cards"]:
        raise store.StudyError("That card no longer exists.")
    engine.record({"type": "review", "id": card_id, "quality": engine.QUALITY[rating]})
    return engine.state()["cards"][card_id]


# ---- what he hears ----------------------------------------------------------------------------------------------
def _say_question(q: dict) -> str:
    text = f"Question {q['n']} of {q['of']}. {q['stem']}"
    if q.get("options"):
        text += " " + " ".join(f"{'ABCD'[n]}: {o}." for n, o in enumerate(q["options"]))
    return text


def _say_result(r: dict, retry: bool) -> str:
    if retry or r.get("hint"):
        return r["feedback"]
    text = r["feedback"]
    if not r["correct"] and r.get("answer") and r["answer"] not in text:
        text += f" The answer: {r['answer']}"
    if r.get("missing") and not r["correct"]:
        text += " You left out: " + "; ".join(r["missing"]) + "."
    return f"{text.rstrip()} ({r['cite']})"


def _friendly(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except store.StudyError as e:
            return str(e)
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def tool(*args, **kwargs):
    """tools.registry.tool, with a StudyError (something he can fix) said to him as it is instead of raised."""
    def decorator(fn):
        return registry.tool(*args, **kwargs)(_friendly(fn))
    return decorator


def _is_owner() -> bool:
    return users.get(_uid()).get("role") == "owner"


@tool("Add study material to a course: pasted notes as text, or a PDF / text / markdown file on this PC by its path. "
      "Creates the course when it is new. Use for 'add these notes to my networking course', 'add this textbook'.",
      {"course": {"type": "string", "description": "the course name, e.g. 'Networking 101'"},
       "title": {"type": "string", "description": "what this material is, e.g. 'Chapter 3 slides'"},
       "text": {"type": "string", "description": "the notes themselves, when pasted or dictated"},
       "path": {"type": "string", "description": "full path of a .pdf, .txt or .md file on this PC"}},
      required=["course"], slow=True, direct=True)
def study_add_material(course: str, title: str = "", text: str = "", path: str = "") -> str:
    if path.strip():
        from pathlib import Path
        from core import pcbridge
        if not pcbridge.local_pc():
            return "I can only read a file by its path on the PC. Upload it on the Study page instead."
        if not _is_owner():
            return "Only the owner can add a file from this PC by its path. Use the Study page to upload it."
        file = Path(path.strip().strip('"'))
        if file.suffix.lower() not in (".pdf", ".txt", ".md", ".markdown"):
            return "I can add PDF, text and markdown files. Export slides or documents as PDF first."
        if not file.is_file():
            return f"I can't find a file at {file}."
        info = store.add_material(course, title or file.stem, file.read_bytes(), file.name)
    elif text.strip():
        if len(text) > MAX_TEXT:
            return "Those notes are too long to add in one piece. Split them by chapter."
        info = store.add_material(course, title or "Notes", text, "notes.md")
    else:
        return "Give me the notes as text or the file's path, or upload it on the Study page."
    if info["duplicate"]:
        return f"{info['title']} is already in {info['course_title']}."
    where = f"{plural(info['pages'], 'page')}, " if info["pages"] else ""
    chapters = f" I found {plural(len(info['chapters']), 'chapter')}." if info["chapters"] else ""
    return (f"Added {info['title']} to {info['course_title']}: {where}{plural(info['chunks'], 'passage')}.{chapters} "
            f"Say 'quiz me on {info['course_title']}' when you're ready.")


@tool("List his study courses with their material, mastery and flashcards due. Use for 'what courses do I have', "
      "'what study material do you have'.", {}, direct=True)
def study_courses() -> str:
    lines = []
    for c in overview()["courses"]:
        p = c["progress"]
        material = "built in" if c.get("builtin") else plural(len(c["materials"]), "document")
        lines.append(f"{c['title']} ({material}, {plural(c['chapters'], 'chapter')}): mastery {p['mastery']} percent, "
                     f"{plural(p['due'], 'card')} due")
    return "Your courses. " + ". ".join(lines) + "."


@tool("Start a quiz from his study material, one question at a time, grounded in the text. Use for 'quiz me on "
      "chapter 3', 'test me on networking', 'give me a practice exam'. Then pass each of his answers to study_answer.",
      {"course": {"type": "string", "description": "course name; empty for his current course"},
       "chapter": {"type": "string", "description": "chapter number or name; empty for the whole course"},
       "count": {"type": "integer", "description": "how many questions (default 5; a practice exam is 15)"},
       "kind": {"type": "string", "enum": ["mixed", "mcq", "short", "scenario", "exam"]}},
      required=[], slow=True, direct=True)
def study_quiz(course: str = "", chapter: str = "", count: int = 5, kind: str = "mixed") -> str:
    out = quiz_start(course, chapter, count, kind)
    scope = out["chapter"] or "the whole course"
    return f"{out['course_title']}, {scope}. {_say_question(out['question'])}"


@tool("Grade his answer to the running quiz question (or flashcard) and move to the next one. Pass his words as they "
      "are: a letter, 'the second one', a sentence, 'I don't know', 'hint'.",
      {"answer": {"type": "string"}}, slow=True, direct=True)
def study_answer(answer: str) -> str:
    s = session()
    if s and s["mode"] == "cards":
        return _card_turn(s, answer)
    out = quiz_answer(answer)
    said = _say_result(out["result"], out["retry"])
    if out["retry"]:
        return said
    if out["result"].get("skipped"):
        said = out["result"]["feedback"] + f" ({out['result']['cite']})"
    if out.get("question"):
        return f"{said} {_say_question(out['question'])}"
    sm = out["summary"]
    if not sm["of"]:
        return f"{said} That's the quiz."
    weak = (" To review: " + ", ".join(dict.fromkeys(m["concept"] for m in sm["missed"])) + ".") if sm["missed"] else " Nothing missed."
    return f"{said} That's the quiz: {sm['right']} of {sm['of']} right.{weak}"


@tool("Stop the running quiz or flashcard review.", {}, direct=True)
def study_stop() -> str:
    out = stop()
    if not out["stopped"]:
        return "No study session is running."
    return f"Stopped. You got {out['right']} of {out['of']} right so far." if out["of"] else "Stopped."


_LEVEL = {"type": "string", "enum": [*ladder.RUNGS, *ladder.MOVES, ""],
          "description": "the rung of the explanation ladder: college, high (school), middle (school), elementary, prek, "
                         "one (a one-year-old); or 'simpler' / 'deeper' to move ONE rung from his last explanation. "
                         "Empty: the rung he is on."}


@tool("Explain a topic from his study material at a rung of the explanation ladder (college, high school, middle "
      "school, elementary, pre-K, one-year-old), with the page cited and a whiteboard drawing on the Study page. Use "
      "for 'explain subnetting from my notes', 'explain DNS like I'm in middle school', 'explain DNS for the exam', "
      "'explain this' (the current quiz question).",
      {"topic": {"type": "string", "description": "the topic; empty for the current question or last topic"},
       "level": _LEVEL, "course": {"type": "string"}},
      required=[], slow=True, direct=True)
def study_explain(topic: str = "", level: str = "", course: str = "") -> str:
    return _say_explained(explain(topic, level, course, fresh=True))


@tool("Explain the last topic again: another way at the same rung (level empty), or one rung simpler or deeper, or at "
      "a rung he names. Use for 'explain it another way', 'I still don't understand' (level 'simpler'), 'more detail' "
      "(level 'deeper'), 'now like I'm one' (level 'one').",
      {"level": _LEVEL}, required=[], slow=True, direct=True)
def study_explain_again(level: str = "") -> str:
    return _say_explained(explain("", level, another=True))


@tool("Draw a topic from his study material on the whiteboard: the explanation built up step by step with shapes, "
      "arrows and moving packets on the Study page (opened on the PC when he is at it), and said out loud. Use for "
      "'show me DNS on the whiteboard', 'draw the TCP handshake', 'put subnetting on the board'.",
      {"topic": {"type": "string", "description": "the topic; empty for the last topic"}, "level": _LEVEL,
       "course": {"type": "string"}},
      required=[], slow=True, direct=True)
def study_whiteboard(topic: str = "", level: str = "", course: str = "") -> str:
    said = _say_explained(explain(topic, level, course, reuse=not topic and not level))
    if OPEN_BOARD():
        return f"{said} It is on the whiteboard on your screen."
    return f"{said} " + ("The whiteboard button is under my reply." if not at_pc.get() else "Open the Study page to watch it on the whiteboard.")


def _say_explained(out: dict) -> str:
    if out.get("repeated"):                                 # one step past either end: say so, do not say the same thing again
        if out.get("edge") == "bottom":
            return ("That is already as simple as I can make it. If you would rather see it than hear it, "
                    "say \"show it on the whiteboard\" and I will draw it.")
        return "That is already the most detailed explanation I have. Ask me for a rung below it, or to draw it on the whiteboard."
    lead = {"bottom": "That is the simplest I have. ", "top": "That is the most detailed I have. "}.get(out.get("edge"), "")
    if out.get("asked") and not lead:
        lead = ladder.SPOKEN[out["level"]][:1].upper() + ladder.SPOKEN[out["level"]][1:] + ". "     # he moved on the ladder: say where he is now
    if out.get("note"):
        lead = f"{out['note']} {lead}"                      # a topic his library has nothing on: said once, at the first rung
    small = out.get("level") in ("elementary", "prek", "one")      # the bottom rungs are said as they are: no extras, no citation
    text = check.spoken(out.get("level") or "", out["text"])        # never a stage direction aloud
    if out.get("general") or out["source"] != "passage":
        if out["limit"] and not small and check.uses_comparison(text, out.get("analogy") or "", out.get("topic") or ""):
            text = f"{check.stop(text)} Where the comparison stops working: {check.stop(out['limit'])}"     # only a comparison that was said
        if out["check"] and not small and not out.get("general"):
            text = f"{check.stop(text)} Check yourself: {out['check']}"
        if out.get("general"):
            return f"{lead}{text}".strip()
    else:
        return f"{lead}In your material's own words: {out['text']} ({out['cite']})"
    if out.get("level") in ("prek", "one"):
        return f"{lead}{text}"
    return f"{lead}{text} ({out['cite']})"


@tool("Make flashcards from a course or chapter of his study material. Use for 'make flashcards for chapter 2'.",
      {"course": {"type": "string"}, "chapter": {"type": "string"},
       "count": {"type": "integer", "description": "how many cards (default 8)"}},
      required=[], slow=True, direct=True)
def study_make_flashcards(course: str = "", chapter: str = "", count: int = 8) -> str:
    out = cards_make(course, chapter, count)
    if not out["made"]:
        return f"You already have cards for everything I can find in {out['chapter'] or out['course_title']}."
    return (f"Made {plural(len(out['made']), 'flashcard')} for {out['chapter'] or out['course_title']}. "
            f"They are due now: say 'review my flashcards'.")


@tool("Review the flashcards that are due today, one at a time (spaced repetition). Use for 'review my flashcards', "
      "'what cards are due'. Then pass each of his answers to study_answer.",
      {"course": {"type": "string"}}, required=[], direct=True)
def study_review_cards(course: str = "") -> str:
    due = cards_due(course)
    if not due:
        return "No flashcards are due. Say 'make flashcards' for a chapter to add some."
    _sessions[_k()] = {"mode": "cards", "course": due[0]["course"], "queue": due[:20], "i": 0, "right": 0,
                         "results": [], "at": time.time()}
    return f"{plural(len(due), 'card')} due. First card. {due[0]['front']}"


@_serial
def _card_turn(s: dict, answer: str) -> str:
    card = s["queue"][s["i"]]
    s["at"] = time.time()
    if _SKIPPED.match(answer or ""):
        s["i"] += 1                                       # skipped: not reviewed, it stays due
        if s["i"] < len(s["queue"]):
            return f"Skipped. Next card. {s['queue'][s['i']]['front']}"
        _sessions.pop(_k(), None)
        return "Skipped. That was the last card due."
    if engine.gave_up(answer):
        rating, said = "again", "Here it is."
    else:
        cover = engine._coverage(card["back"], answer)
        verdict = engine._ask(_model, (
            "A student answered a flashcard from memory. Compare meaning, not wording.\n"
            f"CARD FRONT: {card['front']}\nCARD BACK: {card['back']}\nSTUDENT: {answer[:600]}\n"
            'Reply with JSON only, exactly this shape:\n{"score": 0}\nscore 2 = right, 1 = partly, 0 = wrong.'))
        score = int(verdict["score"]) if isinstance(verdict, dict) and verdict.get("score") in (0, 1, 2) else (2 if cover >= 0.5 else 1 if cover >= 0.25 else 0)
        rating, said = [("again", "Not yet."), ("hard", "Close."), ("good", "Right.")][score]
    card_review(card["id"], rating)
    s["results"].append({"correct": rating == "good"})
    s["right"] += rating == "good"
    s["i"] += 1
    text = f"{said} {card['back']} ({card['cite']})"
    if s["i"] < len(s["queue"]):
        return f"{text} Next card. {s['queue'][s['i']]['front']}"
    _sessions.pop(_k(), None)
    return f"{text} That's every card due: {s['right']} of {len(s['results'])} right."


@tool("His study progress: mastery per chapter, weak concepts, flashcards due, and what to review next. Use for "
      "'how am I doing in networking', 'what should I review', 'what are my knowledge gaps'.",
      {"course": {"type": "string"}}, required=[], direct=True)
def study_progress(course: str = "") -> str:
    c = _course(course)
    p = engine.progress(c["id"])
    if not p["answers"]:
        return f"You haven't answered anything in {c['title']} yet. Say 'quiz me on {c['title']}' to start."
    seen = [x for x in p["chapters"] if x["seen"]]
    best = "; ".join(f"{x['chapter']} {x['mastery']} percent" for x in sorted(seen, key=lambda x: -x["mastery"])[:6])
    weak = ("Weak spots: " + ", ".join(w["concept"] for w in p["weak"][:6]) + ".") if p["weak"] else "No weak spots among what you've answered."
    untouched = [x["chapter"] for x in p["chapters"] if not x["seen"]]
    more = f" Not started: {', '.join(untouched[:5])}." if untouched else ""
    return (f"{c['title']}: {p['right']} of {plural(p['answers'], 'answer')} right, overall mastery {p['mastery']} percent. "
            f"{best}. {weak}{more} {plural(p['due'], 'flashcard')} due.")


@tool("Plan his study up to an exam date: weakest chapters first, spaced reviews, a practice exam at the end. Use for "
      "'my networking exam is on December 5', 'teach me everything I need before this exam', 'make a study plan'.",
      {"course": {"type": "string"},
       "exam_date": {"type": "string", "description": "the exam date as YYYY-MM-DD; empty to reuse the saved date"}},
      required=[], direct=True)
def study_plan(course: str = "", exam_date: str = "") -> str:
    c = _course(course)
    when = exam_date.strip() or c.get("exam_date") or ""
    if when:
        try:
            date.fromisoformat(when)
        except ValueError:
            return "I need the exam date as a calendar date, for example 2026-12-05."
        if exam_date.strip() and course.strip() and not c.get("builtin"):     # only for a course he named: never a guess
            store.set_exam_date(c["id"], when)
    p = engine.plan(c["id"], when or None)
    order = ", then ".join(p["order"][:6])
    if not p["days"]:
        late = " That date has passed." if p["days_left"] is not None else " Tell me the exam date and I'll spread it over the days you have."
        return f"For {c['title']}, study in this order, weakest first: {order}.{late}"
    today = p["days"][0]
    return (f"{plural(p['days_left'], 'day')} to your {c['title']} exam. Order, weakest first: {order}. "
            f"Today: {'; '.join(today['tasks'])}. The day before the exam: {p['days'][-1]['tasks'][0].lower()}. "
            f"The full plan is on the Study page.")


@tool("Find where his study material covers something, with the page. Use for 'where does my textbook talk about "
      "VLANs', 'what do my notes say about DHCP'.",
      {"query": {"type": "string"}, "course": {"type": "string"}}, required=["query"], direct=True)
def study_search(query: str, course: str = "") -> str:
    c = store.find_course(course) if course else None
    hits = store.search(query, c["id"] if c else None, k=2)
    if not hits:
        return f"I found nothing about {query} in your study material."
    return " ".join(f"{store.cite(h)}: {h['text'][:420]}" for h in hits)
