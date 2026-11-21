"""Hybrid recall: what Jarvis knows about a person, found by what the words mean, not by raw substrings.

Before this, memory.recall matched whole-word substrings ("sibling" never found "my sister Marie", "son" matched
"person") and journal.search counted raw words. Now one small derived index covers the person's facts, the household
facts, the shared lessons, their journal summaries with the "Still open" items, and the facts that were corrected
(kept in the memory log, see tools/memory.py; what he had forgotten is erased and never found). An item's score is:

  lexical   SQLite FTS5 BM25 with porter stemming ("lived" finds "live"), squashed to 0..1 by a sigmoid whose midpoint
            and steepness follow the query length (after mem0's scoring)
  aliases   a small relation table: "sibling" asks for sister/brother, "the missus" for wife, "kids" for son/daughter
  entity    a name he used ("Marie") found in the item, damped for hub words that are everywhere ("PC")
  recency   x1.14 within 7 days, x1.08 within 30: it orders items that match alike and never lifts a weak match over a
            strong one (a flat +0.14 did: "this week" in a recent note outranked his wife for "what food does my wife
            like, I'm trying to plan dinner for this week")

The index lives in memory and is rebuilt whenever a store's bytes change: it is derived, per process, never written to
the Syncthing-shared data folder. Without FTS5 (an old SQLite) a plain Python BM25 does the same job.

for_turn() is the automatic recall for one brain turn (intelligence.relevant_memory): nothing for "thanks", at most
8 items or 350 tokens, nothing under half the best score except the items about each person he named (his long lines
name several: "a meal plan for me, my wife and my son"), and a 300 ms limit that fails open (no memory beats a late
answer). The phone app's channel note is not his words and is never searched. Each line says how old it is and where
it came from ("3d ago, you said"), so the model can tell what he said from what was only noted. What it deliberately
does NOT do (design notes from mem0 that don't fit this brain):
  - no "seen" set across a conversation: contextfit strips the lookups attached to earlier questions (only the current
    question's lookup reaches the local model), so an item held back because it was given before would be missing;
  - no identity facts in this block: intelligence.retrieved_notes takes ANY non-empty block as "the lookup already
    happened" and answers about-him questions from it without looking anything up. Who he is goes into the
    <background> instead (tools/memory.context_block keeps the first identity facts saved, however old, within a
    bound: is_identity());
  - lessons only when the question is not about him, for the same reason (a rule for Jarvis is not an answer).
"""
import hashlib
import json
import math
import re
import sqlite3
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from core import storage, users

MAX_ITEMS, MAX_TOKENS, CUT, TIMEOUT = 8, 350, 0.5, 0.3
LINE_CHARS = 300
_RECENCY = ((7, 0.14), (30, 0.08))
_ENTITY, _ENTITY_CAP, _HUB_DAMP, _HUB_SHARE = 0.2, 0.4, 0.3, 0.15

# Asked about one relation, the facts name another word for it. Directional on purpose: "sister" never pulls in the
# brother facts, "mom" never the father ones (an alias table that matches too much recalls the wrong person).
_ALIASES = {
    "sibling": ("sister", "brother"), "siblings": ("sister", "brother", "sisters", "brothers"),
    "sis": ("sister",), "bro": ("brother",),
    "spouse": ("wife", "husband"), "missus": ("wife",), "missis": ("wife",), "mrs": ("wife",), "wifey": ("wife",),
    "hubby": ("husband",),
    "mom": ("mother", "mum"), "mum": ("mother", "mom"), "mama": ("mother", "mom"), "mommy": ("mother", "mom"),
    "mother": ("mom", "mum"), "dad": ("father",), "daddy": ("father", "dad"), "papa": ("father", "dad"),
    "father": ("dad",),
    "parent": ("mother", "father", "mom", "dad"), "parents": ("mother", "father", "mom", "dad"),
    "kid": ("child", "son", "daughter"), "kids": ("child", "children", "son", "daughter"),
    "child": ("kid", "kids", "son", "daughter"), "children": ("child", "kids", "son", "daughter"),
    "family": ("wife", "husband", "sister", "brother", "mother", "father", "mom", "dad", "son", "daughter", "kids",
               "children"),
}
# Words that carry no subject. Questions about the past or about forgetting choose WHICH rows are searched (below),
# so those words are not searched for themselves ("before" would find every lesson that says "ask before ...").
_STOP = set("""a an the and or but if so of to in on at by for from with about into over under as is am are was were be
been being do does did done have has had i me my mine myself we us our ours you your yours he him his she her hers
they them their theirs it its this that these those what whats which who whom whose when where why how can could would
should will shall may might must just also too very really please jarvis sir tell say said know knew think remember
recall anything something everything there here than then now some any all not yes yeah okay ok let lets like get got
go going gonna want need give don didn doesn isn wasn aren won couldn shouldn wouldn haven hasn ll ve re before used
previously formerly earlier originally prior anymore longer forget forgot forgotten delete deleted erase erased
removed still""".split())
_TRIVIAL = set("""thanks thank thx cheers cool great nice sure alright perfect awesome bye goodbye hello hi hey yep yup
nope nah lol haha hmm um uh mhm wow no""".split())
_HUBS = {"jarvis", "pc", "windows", "computer", "laptop", "phone", "claude", "chrome", "edge", "google"}
# "before" asks about the past only at the end of the question or before a name or "that/then/now" ("where did I live
# before?", "before Springfield"): "call my wife before 5" or "before dinner" is about today.
_HISTORY = re.compile(r"(?i:\b(?:used to|previous(?:ly)?|formerly|former|in the past|originally|prior|anymore|no longer|"
                      r"ex[- ]?(?:wife|husband|girlfriend|boyfriend|partner)|(?:my|our|the|his|her|their) old)\b)|"
                      r"\b(?i:before)(?=\s*(?:[?.!,;]|$)|\s+(?:(?i:that|then|this|now|i|we)\b|[A-Z]))")
_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*", re.I)
_IDENTITY = re.compile(r"\b(?:name is|named|called|live[sd]? in|born|birthday|wife|husband|partner|fianc[ée]e?|son|"
                       r"daughter|kids?|children|sister|brother|mother|mom|father|dad|works? (?:at|as|for)|job is)\b", re.I)
_SOURCES = {"said": "you said", "remember": "noted", "journal": "from a past conversation"}
# People he can name: each one he names in a question brings its best fact, however long the question is.
_PEOPLE = {"wife", "husband", "partner", "boyfriend", "girlfriend", "fiance", "fiancee", "son", "daughter", "sister",
           "brother", "mother", "father", "mom", "mum", "dad", "child", "kid", "kids", "children", "baby", "grandma",
           "grandpa", "grandmother", "grandfather", "aunt", "uncle", "cousin", "niece", "nephew", "boss", "manager",
           "roommate", "friend", "dog", "cat"} | set(_ALIASES)
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class Doc:
    key: str                  # unique in the index: "f:<id>" (fact), "c:<id>" (closed), "j:<id>" (journal)...
    kind: str                 # fact | closed | journal | lesson
    text: str
    category: str = ""
    when: str = ""            # ISO date (or date-time) the item was saved, written, or (closed rows) ended
    source: str = ""
    household: bool = False
    row: dict = field(default_factory=dict)   # the stored record: fact row (+ ended_at/by), journal entry, lesson
    body: str = ""            # the words searched, when they differ from the text shown (no "Still open:" label)


@dataclass
class Hit:
    doc: Doc
    score: float
    people: frozenset = frozenset()   # the people he named that this item is about (see pick)


_lock = threading.RLock()
_cache: dict[str, tuple[bytes, "_Index"]] = {}
_fts5: bool | None = None


def is_identity(fact: str) -> bool:
    """Who he is and who his people are (a name, his home, his family, his work)."""
    return bool(_IDENTITY.search(fact))


def machine() -> str:
    """This machine's name for its own files ("pc" / "server"): one writer per synced file, as the journal does."""
    from core import pcbridge
    return "pc" if pcbridge.local_pc() else "server"


# -- query side ---------------------------------------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    """Lower-case ASCII words ("kreyòl" -> "kreyol", as the index's remove_diacritics tokenizer stores it), "wife's" ->
    "wife"."""
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'")).encode("ascii", "ignore").decode()
    return _TOKEN.findall(re.sub(r"'s\b", "", text))


def spoken(text: str) -> str:
    """His words without the phone app's channel note ("[Sent from their Jarvis phone app. Keep replies ...]")."""
    return _CHANNEL.sub("", str(text))


def content_words(text: str) -> list[str]:
    """The words of a question that can be looked up ("thanks" has none; "the missus" has one)."""
    out = []
    for w in _tokens(text):
        if len(w) >= 2 and w not in _STOP and w not in _TRIVIAL and w not in out:
            out.append(w)
    return out


def terms(text: str) -> list[str]:
    """Content words plus their relation aliases, each once."""
    out = content_words(text)
    for w in list(out):
        out += [a for a in _ALIASES.get(w, ()) if a not in out]
    return out


def intent_kinds(text: str) -> set[str]:
    """History rows are searched only when he asks about the past ("where did I live before?"): an ended fact never
    answers a question about now."""
    return {"closed"} if _HISTORY.search(text) else set()


def _entities(text: str) -> set[str]:
    """Names he used: after "my wife/sister/..." (journal._RELATION) or capitalised mid-sentence."""
    from core.journal import _NOT_NAMES, _RELATION
    out = {name.lower() for _, name in _RELATION.findall(text) if name.split()[0] not in _NOT_NAMES}
    for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
        before = text[:m.start()].rstrip()
        word = m.group(0)
        if before and before[-1] not in ".!?:\"" and word not in _NOT_NAMES and word.lower() not in _STOP | _TRIVIAL:
            out.add(word.lower())
    return out


# -- the index ----------------------------------------------------------------------------------------------------------
def _fts5_available() -> bool:
    global _fts5
    if _fts5 is None:
        try:
            db = sqlite3.connect(":memory:")
            db.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
            db.close()
            _fts5 = True
        except sqlite3.Error:
            _fts5 = False
    return _fts5


def _stem(word: str) -> str:
    """The fallback's stems (intelligence._stem, which gives "lives" -> "live" but "lived" -> "liv": a final "e" is
    dropped on both sides so they meet)."""
    from core.intelligence import _stem as stem
    s = stem(word)
    return s[:-1] if len(s) > 3 and s.endswith("e") else s


class _Index:
    def __init__(self, docs: list[Doc]):
        self.docs = docs
        self.lower = [(d.body or d.text).lower() for d in docs]
        self.db = None
        if _fts5_available():
            self.db = sqlite3.connect(":memory:", check_same_thread=False)
            self.db.execute("CREATE VIRTUAL TABLE d USING fts5(body, cat, tokenize='porter unicode61 remove_diacritics 2')")
            self.db.executemany("INSERT INTO d(rowid, body, cat) VALUES (?, ?, ?)",
                                ((i + 1, d.body or d.text, d.category) for i, d in enumerate(docs)))
        else:
            self.tf = [Counter(_stem(w) for w in _tokens(f"{d.body or d.text} {d.category}")) for d in docs]
            self.df = Counter(s for tf in self.tf for s in tf)
            self.avg = sum(sum(tf.values()) for tf in self.tf) / max(len(docs), 1)

    def lexical(self, words: list[str]) -> dict[int, float]:
        """BM25 per matching item (higher is better)."""
        if not words or not self.docs:
            return {}
        if self.db is not None:
            query = " OR ".join(f'"{w}"' for w in words)
            rows = self.db.execute("SELECT rowid, bm25(d, 1.0, 0.5) FROM d WHERE d MATCH ?", (query,)).fetchall()
            return {rowid - 1: max(-score, 0.0) for rowid, score in rows}
        stems, n_docs, out = {_stem(w) for w in words}, len(self.docs), {}
        for i, tf in enumerate(self.tf):
            length, score = sum(tf.values()) or 1, 0.0
            for s in stems:
                if tf.get(s):
                    idf = math.log(1 + (n_docs - self.df[s] + 0.5) / (self.df[s] + 0.5))
                    score += idf * tf[s] * 2.2 / (tf[s] + 1.2 * (0.25 + 0.75 * length / self.avg))
            if score:
                out[i] = score
        return out


def _sources(uid: str) -> list[tuple[str, object, bytes]]:
    from core import journal
    from tools import memory
    user, data = storage.user_dir(uid), storage.DATA
    named = [("facts", user / memory.FILE), ("household", data / memory.HOUSEHOLD_FILE), ("lessons", data / journal.LESSONS)]
    named += [("journal", user / name) for name in (journal.JOURNAL, "journal-pc.json", "journal-server.json")]
    named += [("log", p) for folder in (user, data / "household") if folder.is_dir()
              for p in sorted(folder.glob("memory-log-*.jsonl"))]
    out = []
    for kind, path in named:
        try:
            out.append((kind, path, path.read_bytes()))
        except OSError:
            continue
    return out


def _rows(data: bytes) -> list:
    try:
        rows = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _same(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _events(data: bytes) -> list[dict]:
    out = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _docs(sources) -> list[Doc]:
    from tools.memory import forgotten
    docs, current, journal_ids, history, ended_by, dead = [], set(), set(), {}, {}, {}
    for kind, _, data in sources:                  # what he had forgotten, on either machine (tools/memory.forget)
        for event in _events(data) if kind == "log" else []:
            if event.get("op") == "forget":
                for name in (str(event.get("id", "")), str(event.get("key", ""))):
                    if name:
                        dead[name] = max(dead.get(name, ""), str(event.get("t", "")))
    for kind, _, data in sources:
        if kind in ("facts", "household"):
            for m in _rows(data):
                if isinstance(m, dict) and str(m.get("fact", "")).strip():
                    docs.append(Doc("f:" + str(m.get("id", "")), "fact", str(m["fact"]).strip(), str(m.get("category", "")),
                                    str(m.get("saved", "")), str(m.get("source", "")), kind == "household", m))
                    current.add(_same(str(m["fact"])))
        elif kind == "lessons":
            for item in _rows(data):
                if isinstance(item, dict) and str(item.get("lesson", "")).strip():
                    text = str(item["lesson"]).strip()
                    docs.append(Doc("l:" + hashlib.blake2b(text.encode(), digest_size=6).hexdigest(), "lesson", text,
                                    "lesson", str(item.get("t", "")), "", True, item))
        elif kind == "journal":
            for e in _rows(data):
                if isinstance(e, dict) and e.get("id") not in journal_ids and str(e.get("summary", "")).strip():
                    journal_ids.add(e.get("id"))
                    still = [str(o) for o in e.get("open") or []]
                    summary = str(e["summary"]).strip()
                    # the label is shown, never searched: "open" and "still" would match every entry with open items
                    docs.append(Doc("j:" + str(e.get("id")), "journal",
                                    summary + (f" Still open: {'; '.join(still)}." if still else ""), "",
                                    str(e.get("t", "")), str(e.get("source", "")), False, e,
                                    body=" ".join([summary, *still])))
        elif kind == "log":
            for event in _events(data):
                row, ended = event.get("row"), str(event.get("t", ""))
                if (event.get("op") != "close" or event.get("reason") != "corrected" or not isinstance(row, dict)
                        or not str(row.get("fact", "")).strip() or forgotten(row, ended, dead)):
                    continue
                by = str(event.get("by", ""))
                by = "" if by and forgotten({"fact": by}, ended, dead) else by
                if ended >= ended_by.get(_same(row["fact"]), ("",))[0]:
                    ended_by[_same(row["fact"])] = (ended, by)
                key = "c:" + str(row.get("id", ""))
                history[key] = Doc(key, "closed", str(row["fact"]).strip(), str(row.get("category", "")), ended,
                                   str(row.get("source", "")), str(row.get("id", "")).startswith("h"),
                                   dict(row, ended_at=ended, by=by))

    def now(by: str) -> str:
        """What is true now in place of a closed row: its replacement, or what later replaced that one (Springfield ->
        Denver -> Austin: Springfield's "now" is Austin, never Denver); "" when that chain ends in nothing current."""
        seen = set()
        while by and _same(by) not in current and _same(by) not in seen:
            seen.add(_same(by))
            by = ended_by.get(_same(by), ("", ""))[1]
        return by if by and _same(by) in current else ""
    # A row that is current again (corrected back, or remembered again) is not history. A closed row is also found by
    # what replaced it: "where did I live before Austin?"
    for d in history.values():
        if _same(d.text) not in current:
            then = now(d.row["by"])
            docs.append(Doc(d.key, d.kind, d.text, d.category, d.when, d.source, d.household, dict(d.row, by=then),
                            body=f"{d.text} {then}".strip()))
    return docs


def _index(uid: str) -> _Index:
    sources = _sources(uid)
    digest = hashlib.blake2b(digest_size=16)
    for kind, path, data in sources:
        digest.update(f"{kind}|{path}|{len(data)}|".encode())
        digest.update(data)
    signature = digest.digest()
    cached = _cache.get(uid)
    if cached and cached[0] == signature:
        return cached[1]
    index = _Index(_docs(sources))
    _cache[uid] = (signature, index)
    return index


# -- scoring ------------------------------------------------------------------------------------------------------------
def _squash(raw: float, n_terms: int) -> float:
    """Sigmoid-normalised BM25 (mem0): a longer question needs more matched words to look like a strong match."""
    n = max(1, min(n_terms, 8))
    midpoint, steepness = 1.5 + (n - 1), (1.0 if n <= 3 else 0.7)
    return 1.0 / (1.0 + math.exp(-steepness * (raw - midpoint)))


def _day(when: str) -> date | None:
    try:
        return date.fromisoformat(str(when)[:10])
    except ValueError:
        return None


def _recency(when: str, today: date) -> float:
    day = _day(when)
    if day is None:
        return 0.0
    age = (today - day).days
    return next((bonus for days, bonus in _RECENCY if age <= days), 0.0)


def search(query: str, kinds=("fact",), uid: str | None = None, today: date | None = None) -> list[Hit]:
    """Every item of these kinds that matches the query, best first."""
    query = spoken(query)
    words, names = terms(query), _entities(query)
    if not words:
        return []
    uid, today, kinds = uid or users.current(), today or date.today(), set(kinds)
    content = content_words(query)
    with _lock:
        index = _index(uid)
        raw = index.lexical(words)
        subject = [w for w in words if w not in _HUBS]
        if subject and len(subject) < len(words):
            # "on my PC" is where, not what: an item matching only that ("restart the PC after the update" for "Is
            # Blender still open on my PC?") is no match. Asked about the PC alone, the PC items are.
            about_it = index.lexical(subject)
            raw = {i: score for i, score in raw.items() if i in about_it}
        boosts, about = {}, {}
        for word in (w for w in content if w in _PEOPLE):
            for i in index.lexical([word, *_ALIASES.get(word, ())]):
                about.setdefault(i, set()).add(word)
        for name in names:
            pattern = re.compile(r"\b" + re.escape(name) + r"\b")
            holders = [i for i, low in enumerate(index.lower) if pattern.search(low)]
            share = len(holders) / max(len(index.docs), 1)
            weight = _ENTITY * (_HUB_DAMP if name in _HUBS or share > _HUB_SHARE else 1.0)
            for i in holders:
                boosts[i] = min(boosts.get(i, 0.0) + weight, _ENTITY_CAP)
                if name not in _HUBS:
                    about.setdefault(i, set()).add(name)
        hits = [Hit(index.docs[i], (_squash(score, len(content)) + boosts.get(i, 0.0))
                    * (1.0 + _recency(index.docs[i].when, today)), frozenset(about.get(i, ())))
                for i, score in raw.items() if index.docs[i].kind in kinds]
    # equal scores: what is true now before what ended, then the newer (a closed row's date-time sorts after a date)
    hits.sort(key=lambda h: (h.score, h.doc.kind != "closed", h.doc.when[:10]), reverse=True)
    return hits


def listing(kinds, uid: str | None = None) -> list[Doc]:
    """All items of these kinds, newest first (what was forgotten, what used to be true)."""
    with _lock:
        docs = [d for d in _index(uid or users.current()).docs if d.kind in set(kinds)]
    return sorted(docs, key=lambda d: d.when, reverse=True)


def _age(when: str, today: date) -> str:
    day = _day(when)
    if day is None:
        return "date unknown"
    days = (today - day).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago" if days < 365 else f"{days // 365}y ago"


def render(doc: Doc, today: date | None = None) -> str:
    """One injected line: the item with its age and where it came from."""
    today = today or date.today()
    if doc.kind == "closed":
        now = doc.row.get("by", "")
        tag = f"no longer current since {doc.when[:10]}" + (f"; now: {now}" if now else "")
    elif doc.kind == "journal":
        tag = f"{_age(doc.when, today)}, our conversation"
    elif doc.kind == "lesson":
        tag = f"lesson, {_age(doc.when, today)}"
    else:
        tag = f"{_age(doc.when, today)}, {_SOURCES.get(doc.source, 'noted')}" + (", household" if doc.household else "")
    text = doc.text if len(doc.text) <= LINE_CHARS else doc.text[:LINE_CHARS - 3].rstrip() + "..."
    from core import projectstate as projects      # a project he put on hold is told as on hold, never as his work now (2026-11-21)
    return projects.tagged(f"- ({tag}) {text}")


def _cost(line: str) -> int:
    return (len(line) + 3) // 4            # about four characters a token


def pick(text: str, uid: str, today: date | None = None) -> list[str]:
    """The lines for one turn: the best items within 8 / 350 tokens, none under half the best score, except that each
    person he named is judged on their own: their items within half of the best item about them stay too. His lines
    are long and name several things ("Build a meal plan for me, my wife and my son this week."): an item matching two
    of the words outscores one matching only "wife" by far more than half, and the cut alone would drop his wife and
    son for a note about meal plans."""
    from core.intelligence import about_them
    text, today = spoken(text), today or date.today()
    kinds = {"fact", "journal"} | intent_kinds(text) | (set() if about_them(text) else {"lesson"})
    hits = search(text, kinds, uid, today)
    if not hits:
        return []
    strong = [h for h in hits if h.score >= CUT * hits[0].score]       # a prefix: hits are best first
    best: dict[str, float] = {}
    for hit in hits:
        for person in hit.people:
            best.setdefault(person, hit.score)
    firsts, others, named = [], [], set().union(*(h.people for h in strong))
    for hit in hits[len(strong):]:
        if hit.people - named:                     # the best item about a person no item so far is about
            firsts.append(hit)
            named |= hit.people
        elif any(hit.score >= CUT * best[p] for p in hit.people):
            others.append(hit)
    rank = {id(h): n for n, h in enumerate(hits)}
    chosen, spent = [], 0
    for hit in strong[:1] + firsts + strong[1:] + others:   # every person he named before the rest
        if len(chosen) >= MAX_ITEMS:
            break
        line = render(hit.doc, today)
        if spent + _cost(line) <= MAX_TOKENS:      # a long one that doesn't fit leaves room for shorter ones
            chosen.append((rank[id(hit)], line))
            spent += _cost(line)
    return [line for _, line in sorted(chosen)]


def for_turn(text: str, timeout: float = TIMEOUT) -> list[str]:
    """The automatic recall for one brain turn; [] when there is nothing to look up or it took too long."""
    text = spoken(text)
    if not content_words(text):
        return []                                  # "thanks", "okay", "yes": nothing to look up
    uid, box = users.current(), {}

    def work():
        try:
            box["lines"] = pick(text, uid)
        except Exception as error:                 # a broken store never blocks a voice answer
            box["error"] = f"{type(error).__name__}: {error}"
    worker = threading.Thread(target=work, daemon=True, name="recall")
    worker.start()
    worker.join(timeout)
    if worker.is_alive() or "lines" not in box:
        print(f"[recall] skipped this turn ({box.get('error') or f'over {int(timeout * 1000)} ms'})")
        return []
    return box["lines"]
