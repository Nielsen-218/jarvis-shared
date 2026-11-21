"""Long-term memory: facts Jarvis keeps across conversations and restarts.

Each person has private memories (data/users/<id>/memory.json); household facts
everyone should know live in data/household/memory.json (ids start with "h").

memory.json holds what is true NOW. Nothing is lost when a fact changes: a correction ("My wife's name is Marie" after
"... Maya") closes the old row, which is written to the memory log (memory-log-<machine>.jsonl beside the file, one
writer per machine, as the journal does) with the old and the new text. Recall reads the closed rows for "where did I
live before?" (core/recall.py). A forget() is different: he asked for it to be gone, so its text is erased from
memory.json, the log and the backup, and the log keeps only that something was forgotten (see forget). Rows saved now
also say where they came from ("source": said / remember / journal, and the "evidence" quote of his own words when the
journal extracted them); older rows simply lack those fields.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import date, datetime

from core import storage
from tools.registry import tool

FILE = "memory.json"
HOUSEHOLD_FILE = "household/memory.json"
CATEGORIES = ["personal", "preference", "people", "schedule", "work", "school", "health", "goal", "other"]
_log_lock = threading.Lock()


def _all() -> list[dict]:
    """This person's memories plus the shared household ones."""
    return storage.load(FILE, []) + storage.load(HOUSEHOLD_FILE, [])


def _household(row: dict) -> bool:
    return str(row.get("id", "")).startswith("h")


def _backup(name: str):
    from core import recall
    path = storage.path(name)
    return path.with_name(f"{path.stem}.before-recall-{recall.machine()}.json")


def _save(name: str, rows: list[dict]) -> None:
    """Rows saved by this version carry new fields: the file as it was is copied once first (per machine, so the two
    machines never write one backup), and older code reading the file still finds every row it knew."""
    path, backup = storage.path(name), _backup(name)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    storage.save(name, rows)


def forget_key(text: str) -> str:
    """A forgotten fact is named in the log by this digest of its words, never by its text."""
    return hashlib.blake2b(re.sub(r"\W+", " ", str(text).lower()).strip().encode(), digest_size=8).hexdigest()


def tombstones(folder) -> dict[str, str]:
    """{id or forget_key: when it was forgotten} for every fact forgotten on either machine, from the memory logs in
    this folder. A fact remembered again AFTER that is his again: only what was logged up to then is erased."""
    out: dict[str, str] = {}
    for log in sorted(folder.glob("memory-log-*.jsonl")) if folder.is_dir() else []:
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("op") == "forget":
                for name in (str(event.get("id", "")), str(event.get("key", ""))):
                    if name:
                        out[name] = max(out.get(name, ""), str(event.get("t", "")))
    return out


def forgotten(row, when: str, dead: dict[str, str]) -> bool:
    """The row (as logged at `when`) is one he had forgotten later."""
    if not isinstance(row, dict) or not dead:
        return False
    return any(name in dead and when <= dead[name] for name in (str(row.get("id", "")), forget_key(row.get("fact", ""))))


def _erase(prefix: str, dead: dict[str, str]) -> None:
    """Take every trace of the forgotten facts out of THIS machine's own copies in one store's folder (its memory log
    and its one-time backup; the other machine erases its own the next time it writes, see _log). With _log_lock."""
    from core import recall
    if not dead:
        return
    log = storage.path(prefix + f"memory-log-{recall.machine()}.jsonl")
    if log.exists():
        kept, changed = [], False
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                kept.append(line)
                continue
            if isinstance(event, dict) and event.get("op") != "forget":
                when = str(event.get("t", ""))
                if forgotten(event.get("row"), when, dead):
                    changed = True
                    continue
                if event.get("by") and forgotten({"fact": event["by"]}, when, dead):
                    event, changed = dict(event, by=""), True
                    line = json.dumps(event, ensure_ascii=False)
            kept.append(line)
        if changed:
            temp = log.with_name(log.name + ".tmp")
            temp.write_text("".join(x + "\n" for x in kept), encoding="utf-8", newline="\n")
            os.replace(temp, log)
    backup = _backup(prefix + FILE)
    if backup.exists():                    # copied before the first write, so older than any forget
        try:
            rows = json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = None
        if isinstance(rows, list) and any(forgotten(m, "", dead) for m in rows):
            backup.write_text(json.dumps([m for m in rows if not forgotten(m, "", dead)], ensure_ascii=False, indent=2),
                              encoding="utf-8")


def _log(op: str, row: dict | None = None, *, household: bool | None = None, **more) -> None:
    """One line in the memory log of the store the row belongs to: what was added or closed, and by what (or which
    fact was forgotten: its id and forget_key, never its text). Facts forgotten on the other machine are first erased
    from this machine's own copies too."""
    from core import recall
    prefix = "household/" if (household if household is not None else _household(row or {})) else ""
    event = {"t": datetime.now().isoformat(timespec="seconds"), "op": op, **({"row": row} if row is not None else {}),
             **more}
    path = storage.path(prefix + f"memory-log-{recall.machine()}.jsonl")
    with _log_lock:
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        _erase(prefix, tombstones(path.parent))     # after the line: a forget erases its own fact at once


@tool(
    "Save a lasting fact to long-term memory: who the person is, people in their life, "
    "preferences, routines, goals, important dates. Use it whenever they share something worth "
    "remembering next week, or say 'remember that...'. Don't save trivia or one-off requests.",
    {
        "fact": {"type": "string", "description": "One self-contained fact, e.g. 'His sister Marie lives in Miami.'"},
        "category": {"type": "string", "enum": CATEGORIES},
        "shared": {"type": "boolean", "description": "true for household facts everyone in the home should know "
                   "(address, wifi name, family birthdays, house rules); false for personal ones"},
    },
    direct=True,
)
def remember(fact: str, category: str, shared: bool, *, source: str = "remember", evidence: str = "") -> str:
    """direct: the confirmation is said as written. Asked to re-phrase "Remembered: My sister Marie lives in Miami",
    the small local model said "You live in Miami, sir." (1 run in 3); the fact told back is exact and instant.

    source/evidence (code callers only, never the model): where the fact came from, and for the journal the quote of
    his own words that states it."""
    file = HOUSEHOLD_FILE if shared else FILE
    memories = storage.load(file, [])
    # Both stores are judged: live, the model saved "My wife's name is Maya." as a household fact and the
    # correction "My wife's name is Marie." as a personal one, and the two never met.
    stores = {FILE: storage.load(FILE, []), HOUSEHOLD_FILE: storage.load(HOUSEHOLD_FILE, [])}
    relations = {m["id"]: compare_facts(fact, m["fact"]) for rows in stores.values() for m in rows}
    if any(r in ("same", "narrower") for r in relations.values()):
        return "Already noted, sir."
    slot = _slot(fact)
    if slot and _HEDGED.search(fact):
        # "Maybe my wife's name is Lucy." while "My wife's name is Anna." is on file: a guess about a single-valued
        # slot is not stored beside the confirmed value (it would be recalled as a second answer).
        current = [m["fact"] for rows in stores.values() for m in rows if _slot(m["fact"]) == slot and not _HEDGED.search(m["fact"])]
        if current:
            return f"That sounded uncertain, sir; keeping on file: {told_back(current[0])}"
    # Replaced together: facts this one widens ("Their sister is Marie." -> "Their sister Marie lives in Miami."),
    # guesses it confirms ("Their wife is probably Marie." -> "Their wife is Marie.") and single-valued slots it
    # corrects ("Their wife is Maya." -> "Their wife is Marie."). What was replaced stays on the new record.
    replaced = [m for rows in stores.values() for m in rows if relations[m["id"]] == "wider" or corrects(fact, m["fact"])]
    row = {"id": ("h" if shared else "") + uuid.uuid4().hex[:6], "fact": fact.strip(), "category": category,
           "saved": date.today().isoformat(), "source": source}
    if evidence.strip():
        row["evidence"] = evidence.strip()
    if replaced:
        for name, rows in stores.items():
            kept = [m for m in rows if m not in replaced]
            if len(kept) != len(rows):
                stores[name] = kept
                if name != file:
                    _save(name, kept)
        memories = stores[file]
        # A household row never carries a private text, nor does the household log: everyone's recall reads those.
        row["replaced"] = [m["fact"] for m in replaced if not shared or _household(m)]
        memories.append(row)
        _save(file, memories)
        # The replaced rows are closed, not lost: a correction is history ("where did I live before?"); a fact this
        # one widens or a guess it confirms is still true inside the new row.
        for m in replaced:
            reason = "corrected" if relations[m["id"]] != "wider" else ("confirmed" if _HEDGED.search(m["fact"]) else "widened")
            _log("close", m, reason=reason, by="" if _household(m) and not shared else fact.strip())
        _log("add", row)
        corrected = [m for m in replaced if relations[m["id"]] != "wider"]
        return (f"Noted{' for the household' if shared else ''}, sir: {told_back(fact)}"
                + (f" (this replaces: {'; '.join(told_back(m['fact']) for m in corrected)})" if corrected else ""))
    memories.append(row)
    _save(file, memories)
    _log("add", row)
    return f"Noted{' for the household' if shared else ''}, sir: {told_back(fact)}"


# One live journaling wrote "Their wife is Maya.", "Owner's wife is Maya" and "Wife's name is Maya": the same
# fact three ways. Two facts are the same when their content words are (or one's are all among the other's).
_FILLER = {"the", "and", "his", "her", "their", "owner", "owners", "that", "this", "with", "for", "has", "have", "name",
           "named", "called", "user", "users", "they", "them", "its", "are", "was", "were", "who", "whose", "about"}


def _content(fact: str) -> set[str]:
    text = re.sub("[" + chr(0x2019) + "']s(?![a-z])", "", fact.lower()).replace("'", "")    # "wife's" -> "wife"
    return {w for w in re.findall(r"[a-z0-9]{3,}", text) if w not in _FILLER}


def compare_facts(new: str, old: str) -> str | None:
    """"same" (one fact, two wordings), "wider" (the new one says everything the old one does and more: it replaces
    it), "narrower" (nothing new), or None (different facts)."""
    if new.strip().lower() == old.strip().lower():
        return "same"
    a, b = _content(new), _content(old)
    if not a or not b or min(len(a), len(b)) < 2:
        return None
    if a <= b and _HEDGED.search(old) and not _HEDGED.search(new):
        return "wider"                                # "Their wife is probably Marie." / "...was Marie." confirmed: replaced
    if a == b:
        return "same"
    if b < a and not _HEDGED.search(new):             # "Their wife is NOT Maya." is no wider "Their wife is Maya."
        return "wider"
    if a < b:
        return "narrower"
    return None


# "X is Y" facts about one single-valued slot: a wife, a car, a boss, an address, a phone, a birthday. A new value for
# the same slot is a correction, not a second fact: the old one would otherwise stay and be recalled beside it
# ("What's my wife's name?" with two answers on file). Other facts can legitimately coexist (two sisters).
_SLOTS = ("wife", "husband", "partner", "boss", "manager", "car", "truck", "phone number", "phone", "address", "birthday",
          "anniversary", "job", "employer", "rank", "unit", "duty station", "dog", "cat", "doctor", "dentist", "gym",
          "church", "bank", "wifi name", "wifi", "favorite color", "favourite colour", "shoe size", "height", "weight")
_SLOT_RE = re.compile(r"\b(?:(?:their|his|her|my|your|the owner'?s|owner'?s)\s+)?(" + "|".join(sorted(_SLOTS, key=len, reverse=True))
                      + r")(?:'s name)?\s+is\s+(?:now\s+|called\s+|named\s+)?(.+?)\.?$", re.I)
_HEDGED = re.compile(r"\b(?:probably|maybe|might|may be|perhaps|possibly|likely|apparently|i think|i believe|i guess|"
                     r"not sure|unsure|seems?|supposedly|or|either|used to|formerly|previously|no longer|not|never|isn'?t|"
                     r"wasn'?t|was|were|back then|at the time|in \d{4})\b", re.I)   # past tense is history, not a value


# Where HE lives is single-valued too ("I live in Springfield." -> "I live in Springfield now." after a move), but only when the
# sentence is about him and the value is nothing but a place name: "My sister Marie lives in Miami." is about her,
# "I live in an apartment." / "We live in a townhouse near Lincoln." say what kind of home (not where), "I live in
# Norfolk during the week." and "Lives in Springfield with his wife Maya..." say more than a place; none of them is a
# new value for his home, so none closes the city on file. "We" is the household's home, "I" his own: one person's
# "I live in X" never ends the household's "We live in Y". A state or country alone ("I live in Virginia.") is a
# wider area, not another city: it is its own slot, so "I live in Springfield." never reads as a move away from Virginia.
_PLACE = r"(?:the\s+)?[A-Z][\w.'’-]*(?:\s+(?:(?:of|de|du|des|la|le|del|da|au|aux|on|upon)\s+)?[A-Z][\w.'’-]*)*"
_HOME_RE = re.compile(r"^(?:(?P<who>(?i:i|we|he|they|the owner|owner|the user|user))\s+)?(?i:(?:now|currently|still)\s+)?"
                      r"(?i:lives?\s+in)\s+(?P<place>" + _PLACE + r"(?:,\s*" + _PLACE + r")?)(?i:,?\s+now)?\s*\.?$")
_REGIONS = {s.lower() for s in (
    "Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia Hawaii Idaho Illinois "
    "Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota Mississippi Missouri Montana "
    "Nebraska Nevada Ohio Oklahoma Oregon Pennsylvania Tennessee Texas Utah Vermont Virginia Washington Wisconsin Wyoming "
    "America USA US Canada Mexico Haiti Jamaica Cuba Brazil France Germany Spain Italy Portugal England Scotland Wales "
    "Ireland Japan Korea China India Philippines Nigeria Ghana Kenya Australia Iraq Afghanistan Kuwait Qatar Bahrain "
    "Europe Africa Asia").split()} | {
    "new hampshire", "new jersey", "new mexico", "new york state", "north carolina", "north dakota", "rhode island",
    "south carolina", "south dakota", "west virginia", "puerto rico", "the us", "the usa", "the united states",
    "united states", "the united kingdom", "united kingdom", "the uk", "uk", "south korea", "the dominican republic",
    "dominican republic", "the netherlands", "netherlands", "the bahamas"}
# A plan is not a change: "I'm moving to Denver in March" or "My address is going to be 12 Elm Street" never closes the
# current row (graphiti's rule: only a fact that is true now ends the old one). "will" is matched in lower case only, so a
# man named Will is still a value.
_PLANNED = re.compile(r"(?i:\b(?:going to|gonna|about to|plan(?:s|ning)? to|moving|move to|soon|someday|one day|"
                      r"next (?:week|month|year|spring|summer|fall|autumn|winter)|tomorrow|starting|as of next)\b|['’]ll\b)|"
                      r"\b(?:will|shall)\b")
_PLANNED_MONTH = re.compile(r"\b(?:in|from|starting|by|as of|after)\s+(?:january|february|march|april|may|june|july|august|"
                            r"september|october|november|december)\b", re.I)
_DATE_SLOTS = {"birthday", "anniversary"}           # "My birthday is in March" IS the value, not a plan


def _scoped(text: str) -> tuple[str, str, str] | None:
    """(slot, value, the words that state it): from the slot to the end of the sentence, so what comes before it
    ("I'm going to tell you one more time: my wife's name is Marie.") is not part of the statement."""
    text = text.strip()
    m = _SLOT_RE.search(text)
    if m:
        return m.group(1).lower(), m.group(2), text[m.start():]
    m = _HOME_RE.search(text)
    if not m:
        return None
    place = m.group("place")
    region = re.sub(r"\s+", " ", place.lower()).strip(" .") in _REGIONS
    slot = ("our " if (m.group("who") or "").lower() == "we" else "") + ("home region" if region else "home")
    return slot, place, text


def _slot_value(text: str) -> tuple[str, str] | None:
    found = _scoped(text)
    return found[:2] if found else None


def _slot(text: str) -> str | None:
    found = _slot_value(text)
    return found[0] if found else None


def planned(text: str, slot: str | None = None) -> bool:
    """The sentence states a plan or a future change, not what is true now."""
    return bool(_PLANNED.search(text)) or (slot not in _DATE_SLOTS and bool(_PLANNED_MONTH.search(text)))


# A later clause with a subject of its own is another statement ("..., I'll sell the Civic soon"); one without a
# subject still qualifies the value ("..., starting next month").
_OWN_SUBJECT = re.compile(r"^\s*(?:and\s+|so\s+)?(?:i|i'm|i'll|i've|i'd|we|we're|we'll|you|you'll|he|he'll|she|she'll|"
                          r"they|they'll|it|it'll|that|that'll|this|my|our|his|her|their)\b", re.I)


def _plans(statement: str, slot: str) -> bool:
    """The slot's own statement is a plan: its first clause, or a later clause that qualifies the value ("My address is
    12 Elm Street, starting next month."). "My car is a Tesla Model 3 now, I'll sell the Civic soon." and "My wife's
    name is Marie, I'll tell her you said hi." state a value, then something else."""
    first, *rest = re.split(r"[,;]|\s+but\s+", statement.replace("’", "'"))
    return planned(first, slot) or any(planned(c, slot) for c in rest if not _OWN_SUBJECT.match(c))


def states_now(text: str) -> bool:
    """The sentence states a single-valued slot as true now (not as a plan)."""
    found = _scoped(text)
    return bool(found) and not _plans(found[2], found[0])


def drops_a_plan(fact: str, his: str) -> bool:
    """`fact` states a single-valued slot as true now, while his own words only planned it: "I'm going to live in Denver
    next month." -> "I live in Denver." would close the home he lives in today. The plan wording was lost on the way."""
    return bool(_slot(fact)) and not planned(fact, _slot(fact)) and planned(his) and not states_now(his)


def corrects(new: str, old: str) -> bool:
    """`new` states a different value for the same single-valued slot as `old`, in the present tense and without a hedge.
    "Their car was a Civic" (history), "Their wife is probably Marie" (uncertain), "Their wife is not Maya"
    (a denial, no value) and "My address is going to be 12 Elm Street" (a plan) never replace a fact on file: a valid
    fact is not overwritten on uncertain information. Only the slot's own statement is judged for a plan: "I'm going
    to tell you one more time: my wife's name is Marie." IS a correction (intelligence._contradicts_them relies on it)."""
    if _HEDGED.search(new):
        return False
    a, b = _scoped(new), _slot_value(old)
    if not a or not b or a[0] != b[0] or _plans(a[2], a[0]):
        return False
    return _content(a[1]) != _content(b[1]) and bool(_content(a[1])) and bool(_content(b[1]))


_FLIP = {"my": "your", "mine": "yours", "i": "you", "i'm": "you're", "i've": "you've", "i'll": "you'll", "i'd": "you'd",
         "me": "you", "myself": "yourself", "am": "are", "our": "your", "ours": "yours", "we": "you", "we're": "you're",
         "we've": "you've", "us": "you"}


def told_back(fact: str) -> str:
    """'My sister Marie lives in Miami.' -> 'your sister Marie lives in Miami.' (their words, addressed to them). A fact
    already in the third person ('His sister...') is left as it is. Words he QUOTED stay exactly as he said them:
    'the opening line is exactly "I put my life in your hands."' was read back as "you put your life..." (2026-11-16)."""
    fact = fact.strip()
    quoted = [m.span() for m in re.finditer(r'"[^"]*(?:"|$)|“[^”]*(?:”|$)', fact)]

    def flip(m):
        if any(a <= m.start() < b for a, b in quoted):
            return m.group(0)
        word = m.group(0)
        out = _FLIP.get(word.lower().replace("’", "'"))
        return (out[:1].upper() + out[1:] if word[:1].isupper() and m.start() > 0 and fact[m.start() - 1] in ".!?" else out) if out else word
    text = re.sub(r"\b[\w']+\b", flip, fact.strip())
    return text[:1].lower() + text[1:] if text[:2].lower() in ("yo", "we") or text.lower().startswith("your") else text


def _line(row: dict, note: str = "") -> str:
    household = ", household" if str(row.get("id", "")).startswith("h") else ""
    return f"[{row.get('id', '')}] ({row.get('category', '')}{household}{note}) {row.get('fact', '')}"


def _history_line(doc) -> str:
    now = doc.row.get("by", "")
    return _line(doc.row, f", no longer current since {doc.when[:10]}" + (f"; now: {now}" if now else ""))


_FORGOT = re.compile(r"\b(?:what|which|anything)\b.*\b(?:forg[eo]t(?:ten)?|erased?|deleted?)\b", re.I)


@tool(
    "Search long-term memory (this person's and the household's). Use an empty query to list everything.",
    {"query": {"type": "string", "description": "Keywords, e.g. 'sister' or 'birthday'"}},
)
def recall(query: str) -> str:
    """Hybrid recall (core/recall.py): stemmed BM25 plus relation aliases ("sibling" finds the sister) and recency.
    Facts that ended are listed only when asked about the past ("where did I live before?"), marked as no longer
    current. What he had forgotten is erased, so it is never listed."""
    from core import recall as hybrid
    memories, past = _all(), hybrid.intent_kinds(query)
    if not hybrid.terms(query) and not past and _FORGOT.search(query):
        return "Nothing: what you have me forget is erased, not kept."
    if not hybrid.terms(query):
        lines = [] if past else [_line(m) for m in memories]
        lines += [_history_line(d) for d in hybrid.listing(past)]
        return "\n".join(lines) if lines else ("Nothing remembered yet." if not memories else f"Nothing remembered about '{query}'.")
    hits = hybrid.search(query, {"fact"} | past)
    if not hits:
        return "Nothing remembered yet." if not memories and not past else f"Nothing remembered about '{query}'."
    return "\n".join(_line(h.doc.row) if h.doc.kind == "fact" else _history_line(h.doc) for h in hits)


@tool(
    "Forget a remembered fact (asked to, or it's wrong or outdated). Use recall first to get the id.",
    {"memory_id": {"type": "string"}},
)
def forget(memory_id: str) -> str:
    """"Forgotten" means erased: the row leaves memory.json, and its text leaves this machine's memory log and backup
    (the log keeps only that something was forgotten: its id and forget_key). Recall never brings it back; the other
    machine erases its own copies the next time it writes. Said again, it is simply remembered again."""
    file = HOUSEHOLD_FILE if memory_id.startswith("h") else FILE
    memories = storage.load(file, [])
    gone = next((m for m in memories if m["id"] == memory_id), None)
    if gone is None:
        return f"No memory with id {memory_id}."
    key = forget_key(gone["fact"])
    keep = [dict(m, replaced=[r for r in m["replaced"] if forget_key(r) != key]) if m.get("replaced") else m
            for m in memories if m["id"] != memory_id]
    _save(file, keep)
    _log("forget", household=_household(gone), id=memory_id, key=key)
    return f"Forgotten: {gone['fact']}"


_IDENTITY_KEPT = 40


def _window(rows: list[dict], limit: int) -> list[dict]:
    """The newest `limit` facts, plus the first identity facts saved before those (his name, home, family, work), at
    most _IDENTITY_KEPT of them so the background stays bounded. The first facts saved are exactly those, and a plain
    newest-120 window would be the first to drop them: the model would then guess who his people are (2026-10-30:
    "Don't say she's getting ready to put the kids in school...")."""
    from core import recall
    if len(rows) <= limit:
        return rows
    return [m for m in rows[:-limit] if recall.is_identity(str(m.get("fact", "")))][:_IDENTITY_KEPT] + rows[-limit:]


CORE_CHARS = 4400    # the core background, ~1,000 tokens (core.contextfit.tokens): it sits in the local model's window
CORE_JOURNALS, CORE_LESSONS, CORE_ITEM = 2, 10, 300
CORE_FLOOR = 4       # lessons and behaviour rules each keep this many before the latest conversation goes
# A preference about how he is answered ("prefers brief summaries when asking for a status update"), not a taste of his
# ("prefers black coffee in the morning"): it applies to every turn, whatever is asked.
_HOW_ANSWERED = re.compile(r"\b(?:jarvis|you|your|repl(?:y|ies)|answers?|respon(?:d|ds|se|ses)|summar(?:y|ies|ise|ize)|"
                           r"brief(?:ly)?|concise|verbose|explain\w*|detail(?:s|ed)?|tone|voice|speak(?:s|ing)?|"
                           r"interrupt\w*|confirm\w*|offer\w*|status updates?)\b", re.I)


def _pinned(row: dict) -> bool:
    """A fact every turn needs, whatever is asked: who he is and who his people are (recall.is_identity), and how he wants
    Jarvis to behave (a preference about Jarvis or how he is answered). Everything else is looked up per turn
    (core.recall.for_turn)."""
    from core import recall
    fact = str(row.get("fact", ""))
    return recall.is_identity(fact) or (str(row.get("category", "")).startswith("pref") and bool(_HOW_ANSWERED.search(fact)))


def _core(head: list[str]) -> str:
    """The core background: identity and pinned facts, the household's shared facts, the latest journal summaries with
    what is still open, the newest lessons, within CORE_CHARS. Over it, what per-turn recall brings back goes first (the
    older journal entry, the household facts), then the oldest lessons and behaviour rules, whichever are more, down to
    CORE_FLOOR each (a lesson for 'No, you don't have to.' has no words to be recalled by), then the latest journal
    entry, the rest of those rules, and the identity facts last, the newest first (the first saved, his name, home and
    family, stay: as tools.memory._window keeps them)."""
    from core import journal, recall

    def clip(text: str) -> str:
        text = " ".join(str(text).split())
        return text if len(text) <= CORE_ITEM else text[:CORE_ITEM - 1] + "…"

    mine = [m for m in storage.load(FILE, []) if _pinned(m)]
    identity =[f"- {clip(m['fact'])}" for m in mine if recall.is_identity(str(m.get("fact", "")))]
    behaviour = [f"- {clip(m['fact'])}" for m in mine if not recall.is_identity(str(m.get("fact", "")))]
    household = [f"- {clip(m['fact'])}" for m in storage.load(HOUSEHOLD_FILE, [])]
    past = []
    for e in journal.recent(CORE_JOURNALS):
        when = datetime.fromisoformat(e["t"]).strftime("%a %d %b %H:%M")
        past.append(f"- {when}: {clip(e['summary'])}" + (f" Still open: {clip('; '.join(e['open']))}." if e.get("open") else ""))
    learned = [f"- {clip(x)}" for x in journal.lessons(CORE_LESSONS)]
    titles = ["What you remember about this person (the rest is looked up with each question):",
              "How they want you to behave:", "Household facts (shared):",
              "Your latest conversations with this person (oldest first; pick up open threads naturally, don't recite "
              "them):", "Lessons you've learned from past mistakes (follow them):"]
    sections = [identity, behaviour, household, past, learned]

    from core import projectstate as projects   # a project he put on hold is told as on hold, never as his work now (2026-11-21)

    def render() -> str:
        out = list(head)
        for title, rows in zip(titles, sections):
            if rows:
                out += [title, *(projects.tagged(r) for r in rows)]
        return "\n".join(out)

    def over() -> bool:
        return len(render()) > CORE_CHARS

    def trim(rows: list, keep: int = 0, newest_first: bool = False) -> None:
        while len(rows) > keep and over():
            rows.pop(-1 if newest_first else 0)

    def trim_rules(floor: int) -> None:
        while over():
            longer = [rows for rows in (learned, behaviour) if len(rows) > floor]
            if not longer:
                return
            max(longer, key=len).pop(0)                                 # the oldest of whichever are more

    trim(past, keep=1)                       # the older conversation: recall brings it back when it is asked about
    trim(household)                          # the same
    trim_rules(CORE_FLOOR)
    trim(past)                               # the latest conversation
    trim_rules(0)
    trim(identity, keep=1, newest_first=True)
    return render()


def context_block(limit: int = 120, core_only: bool | None = None) -> str:
    """Background given to the model at the start of every conversation.

    core_only: only the core (see _core), about 1,000 tokens instead of ~3,000. The default is the core wherever the
    rest arrives with each question (intelligence.relevant_memory, the local brain), and everything for the cloud brain."""
    from core import users

    if core_only is None:
        from core import intelligence
        core_only = intelligence.enabled()
    now = datetime.now()
    me = users.get(users.current())
    lines = [f"Current date and time: {now.strftime('%A %d %B %Y, %H:%M')}."]
    from tools.software import is_admin

    lines.append("Admin mode is ON: you run as administrator, so installs, uninstalls and settings changes need no "
                 "Windows approval box." if is_admin() else
                 "Admin mode is OFF: anything needing administrator rights shows Windows' approval box, which only "
                 "someone at the PC can press (the user can turn admin mode on from the tray icon).")
    others = [f"{u.get('name') or uid} ({u.get('role', 'member')})"
              for uid, u in users.all_users().items() if uid != users.current()]
    lines.append(f"You are talking with: {me.get('name') or 'the owner'} ({me.get('role', 'owner')}). "
                 + (f"Others in the household: {', '.join(others)}." if others else "")
                 # "That's tight for a trip with five people in the house" (sandbox 2026-11-21): the count is given, never guessed
                 + " How many people live there is not known unless a fact below says it: never state or guess a number.")
    if core_only:
        return _with_other_machine(_core(lines))
    from core import projectstate as projects
    personal = _window(storage.load(FILE, []), limit)
    shared = _window(storage.load(HOUSEHOLD_FILE, []), limit)
    if personal:
        lines.append("What you remember about this person:")
        lines += [projects.tagged(f"- {m['fact']}") for m in personal]
    if shared:
        lines.append("Household facts (shared):")
        lines += [projects.tagged(f"- {m['fact']}") for m in shared]
    from core import journal

    past = journal.recent(6)
    if past:
        lines.append("Your recent conversations with this person (oldest first; pick up open threads naturally, "
                     "don't recite them):")
        for e in past:
            when = datetime.fromisoformat(e["t"]).strftime("%a %d %b %H:%M")
            lines.append(projects.tagged(f"- {when}: {e['summary']}" + (f" Still open: {'; '.join(e['open'])}." if e.get("open") else "")))
    learned = journal.lessons(25)
    if learned:
        lines.append("Lessons you've learned from past mistakes (follow them):")
        lines += [f"- {x}" for x in learned]
    return _with_other_machine("\n".join(lines))


def _with_other_machine(text: str) -> str:
    """The background plus what this person said on the OTHER machine lately (core.thread: "Recently on their phone"),
    after the core so the core's own budget (CORE_CHARS) is never spent on it. Built once per conversation with the rest
    of the background, so the cached prompt prefix stays the same for the whole conversation."""
    try:
        from core import thread
        recent = thread.background()
    except Exception as error:  # noqa: BLE001 - a missing ledger never costs the background
        print(f"[thread] {type(error).__name__}: {error}")
        recent = ""
    return f"{text}\n{recent}" if recent else text


@tool(
    "Search your journal of past conversations with this person (summaries of what you talked about and did), "
    "for 'what did we talk about last week?', 'remember when I asked you about...', 'what did we decide on X?'.",
    {"query": {"type": "string", "description": "Keywords, e.g. 'proton uninstall', 'birthday gift', 'homelab drive'"}},
)
def recall_conversations(query: str) -> str:
    from core import journal

    found = journal.search(query)
    if not found:
        return "Nothing in the journal about that."
    return "\n".join(f"{datetime.fromisoformat(e['t']).strftime('%a %d %b %Y %H:%M')}: {e['summary']}"
                     + (f" Open: {'; '.join(e['open'])}." if e.get("open") else "") for e in found)


@tool(
    "Save a lesson from a mistake so you never repeat it: when the user corrects you, is annoyed by how you did "
    "something, or a method failed and another worked. Write it as a rule for next time, e.g. 'When the user says "
    "uninstall X, don't ask again: just do it.' Lessons are read before every conversation.",
    {"lesson": {"type": "string"}},
)
def learn_lesson(lesson: str) -> str:
    from core import journal, users

    return journal.add_lesson(lesson, users.current())
