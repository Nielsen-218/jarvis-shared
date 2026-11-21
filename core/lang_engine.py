"""The language teacher's engine: what he knows, what comes next, and whether what he said was right. Pure Python; the
local model is optional everywhere (it only adds a livelier conversation turn and a one-line explanation), and a
model that is down, slow or writes rubbish means the deterministic path, never a crash.

  * Learner profile per language, folded from an append-only event log (one file per machine under the person's data
    folder, like the study tutor): every item with its spaced-repetition state (SM-2, shared with core/study_engine),
    right and wrong counts, weak spots, lessons, minutes, streak.
  * A lesson is a list of steps in five phases: warm-up (due and weak items), new material, guided practice,
    a short free conversation, recap. plan() sizes it to the minutes asked for; the clock ends it on time.
  * Checking tolerates speech recognition: case, accents, tone marks, punctuation, articles, digits for numbers,
    kanji for kana, romaji or pinyin for characters, and near misses.
  * Correction: a miss is first answered with the right form to repeat (a recast); when the same item is missed again
    the reason is given briefly in English and the item comes back later in the lesson.

Speech is described, not made: every reply is a list of parts, plain English strings and target-language parts
({"lang", "text", "roman", "slow"}), so the caller can show them and voice each in the right voice."""
import json
import random
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from core import lang_curriculum as course
from core import storage
from core.study_engine import parse_json, sm2
from core.words import plural

clock = datetime.now                 # tests pin this
STEP_SECONDS = 30                    # one exchange (he hears a line, answers, hears the verdict)
MAX_MINUTES = 60
PHASES = ("warmup", "new", "guided", "talk", "recap")
PHASE_NAMES = {"warmup": "Warm-up", "new": "New material", "guided": "Practice", "talk": "Conversation", "recap": "Recap"}

# ---- reading what he said -----------------------------------------------------------------------------------------
_CONTRACTIONS = (("i'm", "i am"), ("it's", "it is"), ("that's", "that is"), ("what's", "what is"), ("don't", "do not"),
                 ("i'd", "i would"), ("where's", "where is"), ("how's", "how is"))
_LEAD = re.compile(r"^(?:(?:um+|uh+|er+|well|so|okay|ok|oh)\s+)*(?:(?:i think|i guess|maybe|is it|it is|that is|it means|that means|"
                   r"it would be|that would be|you say|the answer is|the word is)\s+)*")
_ARTICLES = {"fr": {"le", "la", "les", "l", "un", "une", "du", "des"}, "es": {"el", "la", "los", "las", "un", "una"}}
_MEANING_NOISE = {"a", "an", "the", "to"}
_GIVE_UP = re.compile(r"^\W*(?:i (?:do not|don'?t|dont) (?:know|remember)|no idea|not sure|idk|i give up|i forgot|(?:just )?tell me(?: the answer)?|"
                      r"what(?:'s| is) the answer|show me the answer)\W*$", re.I)


def gave_up(said: str) -> bool:
    return bool(_GIVE_UP.match(said or ""))


def _cjk(text: str) -> bool:
    return any(ord(c) >= 0x2E80 for c in text)


def norm(text: str) -> str:
    """Lower case, no accents or tone marks on Latin letters, no punctuation, single spaces. Kana and characters are
    kept as they are (their voicing marks are part of the letter)."""
    text = unicodedata.normalize("NFKC", str(text or "")).lower().replace("’", "'").replace("...", " ")
    out = []
    for ch in text:
        if ord(ch) < 0x2E80:
            ch = "".join(c for c in unicodedata.normalize("NFKD", ch) if not ("̀" <= c <= "ͯ"))
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def _plain_english(text: str) -> str:
    low = str(text or "").lower().replace("’", "'")
    for short, long in _CONTRACTIONS:
        low = re.sub(rf"\b{short}\b", long, low)
    return _LEAD.sub("", norm(low))


def _strip_articles(words: list[str], lang: str) -> list[str]:
    kept = [w for w in words if w not in _ARTICLES.get(lang, ())]
    return kept or words


def _verdict(correct=False, close=False, near=False, score=0.0) -> dict:
    return {"correct": correct, "close": close, "near": near, "score": round(score, 3)}


def check_target(item: dict, said: str) -> dict:
    """Did he say the item in the language? -> {"correct", "close" (right but not exact: say it back to him),
    "near" (wrong but nearly), "score"}."""
    lang = item["lang"]
    heard = _LEAD.sub("", norm(said))
    if not heard:
        return _verdict()
    heard_words = _strip_articles(heard.split(), lang)
    heard_flat = "".join(heard_words)
    best = 0.0
    for form in item["accept"]:
        want_words = _strip_articles(norm(form).split(), lang)
        want, want_flat = " ".join(want_words), "".join(want_words)
        if not want_flat:
            continue
        if heard_flat == want_flat:
            return _verdict(True, False, score=1.0)
        if _cjk(want_flat):
            inside = want_flat in heard_flat
        else:
            inside = bool(re.search(rf"(?<!\w){re.escape(want)}(?!\w)", " ".join(heard_words)))
        if inside and (item["open"] or len(heard_flat) <= len(want_flat) * 2 + 6):
            return _verdict(True, False, score=1.0)             # "es buenos días", or the opening with his own ending
        if item["open"]:
            continue
        ratio = SequenceMatcher(None, want_flat, heard_flat).ratio()
        best = max(best, ratio)
        need = 0.8 if _cjk(want_flat) and len(want_flat) >= 3 else 0.82 if len(want_flat) >= 5 else 0.85 if len(want_flat) >= 3 else 2
        if ratio >= need:
            return _verdict(True, True, score=ratio)
    return _verdict(near=best >= 0.6, score=best)


def check_meaning(item: dict, said: str) -> dict:
    """Did he give the English meaning?"""
    heard = [w for w in _plain_english(said).split() if w not in _MEANING_NOISE]
    if not heard:
        return _verdict()
    best = 0.0
    for meaning in item["meanings"]:
        want = [w for w in _plain_english(meaning).split() if w not in _MEANING_NOISE]
        if not want:
            continue
        if heard == want or (set(want) <= set(heard) and len(heard) <= min(len(want) + 2, len(want) * 2)):
            return _verdict(True, False, score=1.0)
        ratio = SequenceMatcher(None, " ".join(want), " ".join(heard)).ratio()
        best = max(best, ratio)
        if ratio >= 0.84 and len("".join(want)) >= 5:
            return _verdict(True, True, score=ratio)
    return _verdict(near=best >= 0.6, score=best)


# ---- the event log -------------------------------------------------------------------------------------------------
_record_lock = threading.Lock()


def _machine():
    from core import thread
    return thread.machine()


def record(event: dict) -> dict:
    """Append one event to THIS machine's file. A file that cannot be read whole is left alone."""
    name = f"languages/events-{_machine()}.json"
    event = dict(event, t=clock().isoformat(timespec="seconds"))
    with _record_lock:
        path = storage.path(name)
        rows = []
        if path.exists():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return event
        rows = rows if isinstance(rows, list) else []
        rows.append(event)
        storage.save(name, rows)
    return event


def events() -> list[dict]:
    folder = storage.user_dir() / "languages"
    rows = []
    for path in sorted(folder.glob("events-*.json")) if folder.is_dir() else []:
        if ".sync-conflict" in path.name or ".corrupt" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows += [r for r in data if isinstance(r, dict) and r.get("t") and r.get("type") and r.get("lang")] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: str(r["t"]))


def _empty() -> dict:
    return {"items": {}, "words": {}, "lessons": 0, "minutes": 0, "last": "", "streak": 0, "accuracy": None, "from_zero": ""}


def fold(rows: list[dict]) -> dict:
    """Replay events -> {lang: {"items": {id: state}, "words": {id: his own added words}, "lessons", "minutes", "last",
    "streak", "accuracy" (of the last lesson)}}."""
    out: dict[str, dict] = {}
    for row in rows:
        prof = out.setdefault(row["lang"], _empty())
        day = str(row["t"])[:10]
        try:
            when = date.fromisoformat(day)
        except ValueError:
            continue
        kind = row["type"]
        if kind == "item" and row.get("item"):
            st = prof["items"].get(row["item"]) or {"ease": 2.5, "interval": 0, "reps": 0, "right": 0, "wrong": 0}
            correct = bool(row.get("correct"))
            try:
                quality = int(row.get("q", 4 if correct else 1))
            except (TypeError, ValueError):
                quality = 4 if correct else 1
            st = sm2(st, quality, when)
            st["right"] += correct
            st["wrong"] += not correct
            st["last_correct"], st["last"] = correct, day
            prof["items"][row["item"]] = st
        elif kind == "word" and row.get("text"):
            wid = f"{row['lang']}:+{norm(row['text'])}"
            prof["words"][wid] = {"id": wid, "text": str(row["text"])[:120], "english": str(row.get("english") or "")[:120],
                                  "roman": str(row.get("roman") or "")[:120], "added": day}
        elif kind == "lesson":
            gap = (when - date.fromisoformat(prof["last"])).days if prof["last"] else None
            prof["streak"] = prof["streak"] if gap == 0 else prof["streak"] + 1 if gap == 1 else 1
            prof["lessons"] += 1
            prof["minutes"] += int(row.get("minutes") or 0)
            prof["last"] = day
            of = int(row.get("of") or 0)
            prof["accuracy"] = round(int(row.get("right") or 0) / of, 2) if of else prof["accuracy"]
    return out


def profile(lang: str) -> dict:
    return fold(events()).get(lang) or _empty()


def is_meaning(lang: str, said: str, items: dict | None = None) -> bool:
    """Is the line, word for word, the English meaning of some statement in the course? (A wrong guess at "what does
    this mean" is still a guess. Course questions such as "what time is it?" are left out: said to Jarvis they are
    far more likely meant for him.)"""
    heard = _plain_english(said)
    return bool(heard) and any(heard == _plain_english(m) for i in (items or course.items(lang)).values() if not i["question"] for m in i["meanings"])


def _weak(st: dict) -> bool:
    return st["wrong"] >= 1 and (not st.get("last_correct") or st["wrong"] > st["right"])


def all_items(lang: str, prof: dict) -> dict:
    """Course items plus the words he asked for himself (they are practised like any other)."""
    found = dict(course.items(lang))
    for wid, w in prof["words"].items():
        found[wid] = {"id": wid, "key": wid, "lang": lang, "unit": "own", "text": w["text"], "roman": w.get("roman", ""),
                      "english": w["english"], "meanings": [w["english"]], "accept": [w["text"]] + ([w["roman"]] if w.get("roman") else []),
                      "open": False, "question": False}
    return found


def due_items(lang: str, prof: dict, today: date | None = None) -> list[dict]:
    """What to review now: weak items first, then the longest overdue."""
    today = (today or clock().date()).isoformat()
    known = all_items(lang, prof)
    rows = [(not _weak(st), st["due"], iid) for iid, st in prof["items"].items() if iid in known and st["due"] <= today]
    rows += [(True, w["added"], wid) for wid, w in prof["words"].items() if wid not in prof["items"]]
    return [known[iid] for _, _, iid in sorted(rows)]


def weak_items(lang: str, prof: dict) -> list[dict]:
    known = all_items(lang, prof)
    rows = sorted(((-(st["wrong"] - st["right"]), -st["wrong"], iid) for iid, st in prof["items"].items() if iid in known and _weak(st)))
    return [dict(known[iid], wrong=prof["items"][iid]["wrong"], right=prof["items"][iid]["right"]) for _, _, iid in rows]


def progress(lang: str, prof: dict | None = None, today: date | None = None) -> dict:
    """Everything the Languages page and "my progress in Spanish" show."""
    prof = prof if prof is not None else profile(lang)
    units, current = [], None
    for u in course.units(lang):
        states = [prof["items"].get(i["id"]) for i in u["items"]]
        seen = sum(1 for s in states if s)
        solid = sum(1 for s in states if s and s.get("last_correct") and s["interval"] >= 6)
        row = {"id": u["id"], "title": u["title"], "can_do": u["can_do"], "total": len(states), "seen": seen, "mastered": solid,
               "status": "done" if solid == len(states) else "started" if seen else "new"}
        if current is None and seen < len(states):
            current = row
        units.append(row)
    seen_all = sum(u["seen"] for u in units)
    current = current or next((u for u in units if u["status"] != "done"), units[-1])
    n = units.index(current) + 1
    level = "Not started" if not seen_all else f"Beginner, unit {n} of {len(units)}"
    due = due_items(lang, prof, today)
    return {"lang": lang, "name": course.LANGUAGES[lang]["name"], "native": course.LANGUAGES[lang]["native"], "level": level,
            "unit": current, "units": units, "learned": seen_all, "total": sum(u["total"] for u in units),
            "due": [_public(i) for i in due], "weak": [_public(i) for i in weak_items(lang, prof)[:12]],
            "lessons": prof["lessons"], "minutes": prof["minutes"], "streak": prof["streak"], "last": prof["last"],
            "accuracy": prof["accuracy"], "words": len(prof["words"])}


def _public(item: dict) -> dict:
    return {k: item[k] for k in ("id", "text", "roman", "english", "unit") if k in item} | (
        {"wrong": item["wrong"], "right": item["right"]} if "wrong" in item else {})


# ---- planning a lesson ---------------------------------------------------------------------------------------------
def beginner(prof: dict) -> bool:
    """Slow speech and more English while he has seen fewer than two units' worth."""
    return len(prof["items"]) < 16


def plan(lang: str, minutes: int, prof: dict, today: date | None = None, from_zero: bool = False) -> list[dict]:
    """The steps of one lesson, sized to the minutes: [{"phase", "kind": "say"|"repeat"|"meaning"|"talk", "item"}]."""
    minutes = max(2, min(MAX_MINUTES, int(minutes or 10)))
    budget = max(5, minutes * 60 // STEP_SECONDS)
    units = course.units(lang)
    known = all_items(lang, prof)
    steps: list[dict] = []
    talks = 1 if minutes < 10 else 2 if minutes < 25 else 3
    room = budget - talks

    due = [] if from_zero else due_items(lang, prof, today)
    warm = due[:min(len(due), max(2, room // 4), 8)]
    steps += [{"phase": "warmup", "kind": "say", "item": i["id"]} for i in warm]

    # How much new material: about a fifth of the lesson, more after a strong lesson, less after a hard one.
    fresh_n = max(2, min(8, room // 5))
    if prof["accuracy"] is not None and not from_zero:
        fresh_n = min(10, fresh_n + 2) if prof["accuracy"] >= 0.85 else max(1, fresh_n - 1) if prof["accuracy"] < 0.6 else fresh_n
    if len(due) > 12 and not from_zero:
        fresh_n = min(fresh_n, 2)                                   # a backlog of reviews comes before new words
    order = [i for u in units for i in u["items"]]
    fresh = [i for i in order if from_zero or i["id"] not in prof["items"]][:fresh_n]
    fresh = fresh[:max(0, (room - len(steps)) // 3)] or fresh[:1]
    steps += [{"phase": "new", "kind": "repeat", "item": i["id"]} for i in fresh]
    guided = [{"phase": "guided", "kind": "meaning", "item": i["id"]} for i in fresh]
    guided += [{"phase": "guided", "kind": "say", "item": i["id"]} for i in fresh]
    used = {s["item"] for s in steps}
    extra = [i for i in weak_items(lang, prof) if i["id"] not in used] if not from_zero else []
    extra += [known[iid] for iid, st in sorted(prof["items"].items(), key=lambda kv: kv[1].get("last", "")) if iid in known and iid not in used
              and iid not in {e["id"] for e in extra}] if not from_zero else []
    pool = [{"phase": "guided", "kind": "say", "item": i["id"]} for i in extra]
    again = [{"phase": "guided", "kind": "say", "item": i["id"]} for i in fresh]          # repetition when nothing else is left
    while len(steps) + len(guided) < room and (pool or again):
        guided.append(pool.pop(0) if pool else again.pop(0))
    steps += guided[:max(0, room - len(steps))]

    unit_id = (fresh[-1] if fresh else warm[-1] if warm else order[0])["unit"]
    unit = next((u for u in units if u["id"] == unit_id), units[0])
    for n in range(talks):
        steps.append({"phase": "talk", "kind": "talk", "item": unit["reply"], "ask": unit["ask"] if n == 0 else None, "unit": unit["id"]})
    return steps


# ---- a running lesson ----------------------------------------------------------------------------------------------
def start(lang: str, minutes: int = 10, from_zero: bool = False, prof: dict | None = None) -> dict:
    prof = prof if prof is not None else profile(lang)
    minutes = max(2, min(MAX_MINUTES, int(minutes or 10)))
    return {"lang": lang, "minutes": minutes, "started": clock(), "steps": plan(lang, minutes, prof, from_zero=from_zero), "i": 0, "tries": 0,
            "right": 0, "of": 0, "misses": {}, "taught": [], "missed": [], "slow": beginner(prof) or from_zero,
            "history": {iid: st["wrong"] for iid, st in prof["items"].items()}, "items": all_items(lang, prof),
            "first": not prof["items"] or from_zero, "done": False, "last": [], "talk": []}


def _target(item: dict, slow: bool = False) -> dict:
    return {"lang": item["lang"], "text": item["text"].replace("...", "").strip() if item["open"] else item["text"],
            "roman": item["roman"].replace("...", "").strip(), "slow": slow, "show": item["text"]}


def step(s: dict) -> dict | None:
    return s["steps"][s["i"]] if not s["done"] and s["i"] < len(s["steps"]) else None


def phase(s: dict) -> str:
    st = step(s)
    return st["phase"] if st else "recap"


def _name(s: dict) -> str:
    return course.LANGUAGES[s["lang"]]["name"].replace("Mandarin Chinese", "Chinese")


def prompt(s: dict) -> list:
    """What Jarvis says to put the current step on the table."""
    st = step(s)
    if st is None:
        return []
    item, name = s["items"][st["item"]], _name(s)
    if st["kind"] == "repeat":
        tail = " and add your own ending" if item["open"] else ""
        parts = [f"New: \"{item['english']}\" is", _target(item, s["slow"]), f"Say it after me{tail}."]
    elif st["kind"] == "meaning":
        parts = ["What does this mean?", _target(item, s["slow"])]
    elif st["kind"] == "say":
        parts = [f"How do you say \"{item['english']}\" in {name}?"]
    else:
        ask = s["items"].get(st.get("ask") or "")
        if ask:
            parts = [f"Now a little conversation. Answer me in {name}.", _target(ask, s["slow"])]
        else:
            unit = next(u for u in course.units(s["lang"]) if u["id"] == st["unit"])
            parts = [f"Your turn to talk. In {name}, with the words you know, {unit['can_do']}."]
    s["last"] = parts
    return parts


def opening(s: dict) -> list:
    name, st = _name(s), step(s)
    first = PHASE_NAMES[st["phase"]].lower() if st else "recap"
    if s["first"]:
        lead = f"{name} from the very beginning, {plural(s['minutes'], 'minute')}. I say a phrase, you say it back, then I check what stayed."
    else:
        lead = f"{name}, {plural(s['minutes'], 'minute')}." + (f" We start with a {first}." if first == "warm-up" else "")
    return [lead] + prompt(s)


def _advance(s: dict) -> None:
    s["i"] += 1
    s["tries"] = 0


def _requeue(s: dict, item_id: str) -> None:
    """A missed item comes back three steps later, before the conversation, once."""
    if s["misses"].get(item_id, 0) > 2:
        return
    at = min(s["i"] + 3, next((n for n, st in enumerate(s["steps"]) if n > s["i"] and st["phase"] == "talk"), len(s["steps"])))
    s["steps"].insert(at, {"phase": s["steps"][s["i"]]["phase"], "kind": "say", "item": item_id, "again": True})


def _out_of_time(s: dict) -> bool:
    return (clock() - s["started"]).total_seconds() >= s["minutes"] * 60


def _why(s: dict, item: dict, said: str, model) -> str:
    """One short English sentence about the slip: the local model's when it gives one, else the unit's own note."""
    tip = next((u["tip"] for u in course.units(s["lang"]) if u["id"] == item["unit"]), "")
    if model is not None and said.strip():
        try:
            got = parse_json(model(
                f"A beginner learning {_name(s)} was asked to say \"{item['english']}\". The right answer is \"{item['text']}\". "
                f"The learner said \"{said[:120]}\". In ONE short English sentence (at most 22 words), say what to change. "
                'Reply with JSON only: {"why": "..."}'))
            why = " ".join(str((got or {}).get("why") or "").split()) if isinstance(got, dict) else ""
            if 8 <= len(why) <= 200 and not _cjk(why):
                return why if why[-1] in ".!?" else why + "."
        except Exception:                                    # noqa: BLE001 - the note below is always there
            pass
    words = item["text"].split()
    return tip or (f"It is {plural(len(words), 'word')}: {', '.join(words)}." if len(words) > 1 else "Listen to the whole word and copy its rhythm.")


def _talk_turn(s: dict, st: dict, said: str, model) -> list:
    """Free conversation: whatever he says is taken; the model, when it is there, answers at his level and offers a
    corrected version; without it he hears one good way to say it."""
    sample, name = s["items"][st["item"]], _name(s)
    s["talk"].append(said[:200])
    if model is not None:
        known = [s["items"][i]["text"] for i in list(dict.fromkeys(s["taught"] + [x["item"] for x in s["steps"][:s["i"]]]))][:30]
        try:
            got = parse_json(model(
                f"You are a patient {name} teacher talking with a complete beginner. The learner just said: \"{said[:200]}\". "
                f"Phrases the learner knows: {'; '.join(known) or sample['text']}. Reply with JSON only: "
                '{"recast": "the learner\'s sentence in correct ' + name + ', or empty if it was already correct or not in ' + name + '", '
                '"reply": "one short friendly ' + name + ' sentence (at most 8 words) using mostly those phrases", "english": "that reply in English"}'))
            if isinstance(got, dict):
                recast, reply, english = (" ".join(str(got.get(k) or "").split())[:160] for k in ("recast", "reply", "english"))
                if reply and english and not re.search(r"[{}\[\]<>]", recast + reply + english):
                    parts: list = []
                    if recast and norm(recast) != norm(said):
                        parts += ["You could say:", {"lang": s["lang"], "text": recast, "roman": "", "slow": s["slow"], "show": recast}]
                    return parts + [{"lang": s["lang"], "text": reply, "roman": "", "slow": s["slow"], "show": reply}, f"That means: {english.rstrip('.')}."]
        except Exception:                                    # noqa: BLE001 - the scripted line below
            pass
    return ["Good. One way to say it:", _target(sample, s["slow"])]


def answer(s: dict, said: str, model=None) -> dict:
    """Take his line for the step on the table. -> {"parts": what Jarvis says (verdict, then the next step or the
    recap), "correct": bool or None, "done": bool}."""
    st = step(s)
    if st is None:
        return {"parts": finish(s), "correct": None, "done": True}
    item = s["items"][st["item"]]
    parts: list = []
    correct = None
    if st["kind"] == "talk":
        parts = _talk_turn(s, st, said, model)
        _advance(s)
    else:
        gave = gave_up(said)
        v = _verdict() if gave else check_meaning(item, said) if st["kind"] == "meaning" else check_target(item, said)
        correct = v["correct"]
        first_try = s["tries"] == 0
        if correct:
            quality = (4 if st["kind"] == "repeat" else 5) if first_try and not st.get("again") else 3
            if first_try:
                s["right"] += 1
            if st["kind"] == "repeat" and item["id"] not in s["taught"]:
                s["taught"].append(item["id"])
            if st["kind"] == "meaning":
                parts = ["Right."] if not v["close"] else [f"Right: {item['english']}."]
            else:
                parts = ["Good."] if not v["close"] else ["Good:", _target(item, s["slow"])]
            record({"type": "item", "lang": s["lang"], "item": item["id"], "correct": True, "q": quality, "kind": st["kind"]})
            s["of"] += first_try
            _advance(s)
        else:
            s["misses"][item["id"]] = s["misses"].get(item["id"], 0) + 1
            repeated = s["misses"][item["id"]] + s["history"].get(item["id"], 0) >= 2
            lead = "It is" if gave else "Almost. It is" if v["near"] else "Not quite. It is"
            if st["kind"] == "meaning":
                parts = [f"It means \"{item['english']}\"."]
                final = True                                  # the meaning has been given away: no second try
            elif first_try and not gave and not repeated:
                parts = [lead, _target(item, True), "Once more."]              # the recast: hear it, say it again
                final = False
            else:
                parts = [lead, _target(item, True)]
                if repeated and not gave:
                    parts.append(_why(s, item, said, model))                  # the same slip again: say why, briefly
                final = True
            if final:
                if first_try:
                    s["of"] += 1
                record({"type": "item", "lang": s["lang"], "item": item["id"], "correct": False, "q": 1, "kind": st["kind"]})
                if item["id"] not in s["missed"]:
                    s["missed"].append(item["id"])
                if st["kind"] == "repeat" and item["id"] not in s["taught"]:
                    s["taught"].append(item["id"])
                _requeue(s, item["id"])
                _advance(s)
            else:
                s["of"] += 1
                s["tries"] = 1
                s["last"] = parts
                return {"parts": parts, "correct": False, "done": False, "retry": True}
    if step(s) is None or _out_of_time(s):
        return {"parts": parts + finish(s), "correct": correct, "done": True}
    before = st["phase"]
    nxt = prompt(s)
    if step(s)["phase"] != before and step(s)["phase"] != "talk":
        nxt = [f"{PHASE_NAMES[step(s)['phase']]}."] + nxt
        s["last"] = nxt
    return {"parts": parts + nxt, "correct": correct, "done": False}


def skip(s: dict) -> dict:
    """He does not want this one: no mark against him, it simply comes later in another lesson."""
    if step(s) is None:
        return {"parts": finish(s), "done": True}
    _advance(s)
    if step(s) is None or _out_of_time(s):
        return {"parts": ["Skipped."] + finish(s), "done": True}
    return {"parts": ["Skipped."] + prompt(s), "done": False}


def repeat(s: dict, slower: bool = False) -> list:
    """The last line again; slower makes every target-language part slow."""
    parts = s.get("last") or prompt(s)
    return [dict(p, slow=True) if isinstance(p, dict) and slower else p for p in parts]


def finish(s: dict) -> list:
    """Close the lesson: record it once and give the recap."""
    if s["done"]:
        return []
    s["done"] = True
    spent = max(1, round((clock() - s["started"]).total_seconds() / 60))
    record({"type": "lesson", "lang": s["lang"], "minutes": min(spent, s["minutes"] + 5), "right": s["right"], "of": s["of"],
            "new": len(s["taught"])})
    parts: list = [f"That's the lesson: {s['right']} of {s['of']} right." if s["of"] else "That's the lesson."]
    if s["taught"]:
        parts.append("New today:")
        for iid in s["taught"][:4]:
            parts.append(_target(s["items"][iid]))
    if s["missed"]:
        parts.append("To practise: " + ", ".join(f"\"{s['items'][i]['english']}\"" for i in s["missed"][:4]) + ".")
    due = (clock().date() + timedelta(days=1)).isoformat()
    parts.append(f"I will bring these back tomorrow for review." if s["taught"] or s["missed"] else "Nothing new to review yet.")
    s["next_review"] = due
    return parts


# ---- looking things up ---------------------------------------------------------------------------------------------
def find_phrase(lang: str, english: str, prof: dict | None = None) -> dict | None:
    """The course item (or one of his own words) for an English meaning: "good morning" -> the item."""
    want = [w for w in _plain_english(english).split() if w not in _MEANING_NOISE]
    if not want:
        return None
    best, best_ratio = None, 0.0
    for item in all_items(lang, prof or _empty()).values():
        for meaning in item["meanings"]:
            have = [w for w in _plain_english(meaning).split() if w not in _MEANING_NOISE]
            if have == want:
                return item
            ratio = SequenceMatcher(None, " ".join(have), " ".join(want)).ratio()
            if ratio > best_ratio:
                best, best_ratio = item, ratio
    return best if best_ratio >= 0.88 else None


def find_meaning(lang: str, phrase: str, prof: dict | None = None) -> dict | None:
    """The item a phrase in the language is: "buenos dias" -> the item."""
    for item in all_items(lang, prof or _empty()).values():
        if check_target(dict(item, open=False), phrase)["correct"]:
            return item
    return None


def translate(lang: str, text: str, to_english: bool, model) -> dict | None:
    """A phrase the course does not have, from the local model: {"text", "roman", "english"} or None."""
    if model is None or not text.strip():
        return None
    name = course.LANGUAGES[lang]["name"]
    extra = ' "roman": "pinyin with tone marks",' if lang == "zh" else ' "roman": "romaji",' if lang == "ja" else ""
    ask = (f"What does the {name} phrase \"{text[:120]}\" mean in English?" if to_english
           else f"How does a native speaker say \"{text[:120]}\" in everyday {name}?"
           + (" Use the official Haitian Creole orthography." if lang == "ht" else ""))
    try:
        got = parse_json(model(ask + ' Reply with JSON only: {"text": "the ' + name + ' phrase",' + extra + ' "english": "the English meaning"}'))
    except Exception:                                        # noqa: BLE001
        return None
    if not isinstance(got, dict):
        return None
    out = {k: " ".join(str(got.get(k) or "").split())[:160] for k in ("text", "roman", "english")}
    return out if out["text"] and out["english"] and not re.search(r"[{}\[\]<>]", out["text"] + out["english"]) else None


_rng = random.Random()
