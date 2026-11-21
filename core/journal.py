"""Jarvis's journal: what makes him *yours* over time.

When a conversation ends (you go quiet, or Jarvis restarts), a small, cheap model reads it
and writes down:
  - a short summary (what you talked about, what got done, what's still open), so the next
    conversation can pick up where you left off ("did the Proton uninstall work out?");
  - lasting facts about you it picked up (likes, people, plans, habits) -> long-term memory;
  - lessons from mistakes (something failed, then another way worked, or you corrected him)
    -> a shared lessons list he reads before every task;
  - what made you laugh / your sense of humor -> memory, so his jokes fit you.

Each person's journal is private (data/users/<id>/journal.json); lessons are shared by the
household because they're about the PC and the homelab (data/household/lessons.json).
Passwords, keys, card numbers and similar are never written down.
"""
import json
import re
import threading
import uuid
from datetime import datetime

from core import storage, usage, users

MODEL = "claude-haiku-5-5"  # fast and cheap: about a tenth of a cent per conversation
JOURNAL = "journal.json"   # legacy shared file: read-only now (both machines writing it made Syncthing conflicts)
LESSONS = "household/lessons.json"
MAX_JOURNAL, MAX_LESSONS = 400, 150
MIN_USER_TURNS = 2  # "what time is it?" alone isn't worth a journal entry
NOTES_WAIT_S = 4 * 3600.0   # the local notes wait this long for a free card (his turns, a game), then the plain record


def _mine() -> str:
    """This machine's own journal file. The PC and the laptop each write only their own, so Syncthing never has
    two versions of one file to choose between (the loser used to vanish into a .sync-conflict copy)."""
    from core import pcbridge
    return "journal-pc.json" if pcbridge.local_pc() else "journal-server.json"


def _append(entry: dict) -> None:
    rows = storage.load(_mine(), [])
    rows.append(entry)
    storage.save(_mine(), rows[-MAX_JOURNAL:])


def entries() -> list[dict]:
    """Both machines' journals plus the legacy shared one, oldest first, without duplicates."""
    seen, out = set(), []
    for name in (JOURNAL, "journal-pc.json", "journal-server.json"):
        for e in storage.load(name, []):
            if isinstance(e, dict) and e.get("id") not in seen:
                seen.add(e.get("id"))
                out.append(e)
    return sorted(out, key=lambda e: e.get("t", ""))

_client = None
_SECRET = re.compile(r"(password|passcode|api[ _-]?key|token|secret|\b\d{12,19}\b|\bsk-[a-z0-9-]{10,})", re.I)

PROMPT = """You are the memory of J.A.R.V.I.S., a personal assistant. Below is a conversation between Jarvis \
and {who}. Write Jarvis's private notes about it as JSON with exactly these keys:

"summary": 1-3 plain sentences: what was talked about, what got done, how it ended.
(Jarvis's short PC lines, like "Closed Paint." or "Should I close Edge?", are outcomes he reported or questions he asked; \
a reply marked [interrupted ...] was cut off, so that request was not finished.)
"open": list of short things left unfinished or promised for later (may be empty).
"facts": list of lasting facts about {who} worth knowing next month (preferences, people in their life, \
plans, routines, projects, likes/dislikes, how they like Jarvis to behave). Each: {{"fact": "...", \
"category": one of {cats}, "evidence": "the words {who} said that state it, copied exactly from a USER line"}}. \
Only things that will still be true and useful in a month; not details of today's task (what was installed, \
deleted or looked up), not guesses, never passwords/keys/account or card numbers. A fact with no USER words to \
quote is a guess: leave it out (what JARVIS said, or what {who} told Jarvis not to say, is never evidence).
"lessons": list of reusable lessons for Jarvis from mistakes in THIS conversation: something failed and \
another way worked, or {who} corrected Jarvis or was annoyed by how he did something. Write each as a \
rule Jarvis can follow next time, e.g. "Proton apps are installed per-user: winget can't remove them from \
admin mode; use the app's own uninstaller." Empty if nothing went wrong.
"humor": list of notes on {who}'s own sense of humor from this conversation (what they laughed at, what \
they joked about, their style). Only what {who} actually said or did. Empty if nothing.

Reply with the JSON only.

Conversation:
{transcript}"""


def _text_of(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for b in content:
        t = b.get("type") if isinstance(b, dict) else getattr(b, "type", "")
        get = (lambda k, d=None: b.get(k, d)) if isinstance(b, dict) else (lambda k, d=None: getattr(b, k, d))
        if t == "text":
            text = get("text", "")
            from core import asides
            if text.lstrip().startswith(asides.TAG):     # what Jarvis said unprompted: his words, shown as a note
                said = asides.lines(text)
                if said:
                    out.append("(Jarvis had said, unprompted: " + " / ".join(said) + ")")
            elif not text.lstrip().startswith(("<background>", "<recently-elsewhere>")):   # the other machine journals its own
                out.append(text)
        elif t == "tool_use":
            out.append(f"(Jarvis used {get('name')}: {json.dumps(get('input', {}))[:160]})")
        elif t == "tool_result":
            c = get("content", "")
            first = (c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict)))
            first = first.strip().splitlines()[0][:200] if first.strip() else ""
            if first:
                out.append(f"(result: {first})")
    return out


def transcript(messages: list[dict]) -> tuple[str, int]:
    lines, turns = [], 0
    for m in messages:
        role = m.get("role")
        parts = _text_of(m.get("content", ""))
        if role == "user" and any(not p.startswith("(") for p in parts):
            turns += 1
        for p in parts:
            if p.startswith("("):
                lines.append(f"  {p}")
            else:
                lines.append(f"{'USER' if role == 'user' else 'JARVIS'}: {p.strip()}")
    return "\n".join(lines)[-30000:], turns


def _ask(prompt: str) -> dict:
    from core.intelligence import local_only
    if local_only():
        return {}  # Brain uses archive_local; never create a paid summarizer.
    global _client
    import anthropic

    if _client is None:
        _client = anthropic.Anthropic()
    resp = _client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    usage.record(MODEL, resp.usage)
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _notes_object(text)


def _notes_object(text: str) -> dict:
    """Extract one complete JSON object from a model reply without trusting prose around it.

    Models occasionally add a code fence or a sentence despite the schema
    instruction.  ``json.loads`` then discarded the whole journal turn (and
    the cloud archive path let the decoder error escape).  ``raw_decode``
    accepts a complete object followed by harmless prose, but never repairs
    malformed JSON or accepts a non-object result.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _clean(items, limit=8) -> list:
    out = []
    for x in (items or [])[:limit]:
        s = x.get("fact", "") if isinstance(x, dict) else str(x)
        if s.strip() and not _SECRET.search(s):
            out.append(x)
    return out


def archive(uid: str, messages: list[dict]) -> dict | None:
    """Write the journal entry, facts, lessons and humor notes for one finished conversation."""
    text, turns = transcript(messages)
    if turns < MIN_USER_TURNS:
        return None
    from tools import memory

    with users.acting_as(uid):
        who = users.get(uid).get("name") or "the owner"
        notes = _ask(PROMPT.format(who=who, cats=memory.CATEGORIES, transcript=text))
        if not notes:
            return None
        summary = str(notes.get("summary", "")).strip()
        if summary and not _SECRET.search(summary):
            _append({"id": uuid.uuid4().hex[:8], "t": datetime.now().isoformat(timespec="minutes"),
                     "summary": summary, "open": [str(o) for o in _clean(notes.get("open"), 5)]})
        for f in _clean(notes.get("facts")):
            if isinstance(f, dict):
                cat = f.get("category") if f.get("category") in memory.CATEGORIES else "other"
                memory.remember(f["fact"], cat, False, source="journal")
        for h in _clean(notes.get("humor"), 3):
            memory.remember(f"Humor: {h}", "preference", False, source="journal")
        for lesson in _clean(notes.get("lessons"), 5):
            add_lesson(str(lesson), uid)
    return notes


def add_lesson(lesson: str, uid: str = "") -> str:
    lesson = lesson.strip()
    if not lesson or _SECRET.search(lesson):
        return "Not saved."
    lessons = storage.load(LESSONS, [])
    key = re.sub(r"\W+", " ", lesson.lower()).strip()
    if any(re.sub(r"\W+", " ", item["lesson"].lower()).strip() == key for item in lessons):
        return "Already learned."
    lessons.append({"lesson": lesson, "t": datetime.now().isoformat(timespec="minutes"), "by": uid})
    storage.save(LESSONS, lessons[-MAX_LESSONS:])
    return "Lesson saved."


def archive_async(uid: str, messages: list[dict]) -> None:
    def run():
        try:
            archive(uid, messages)
        except Exception as e:
            print(f"[journal] couldn't write the journal entry: {type(e).__name__}: {e}")
    threading.Thread(target=run, daemon=True, name="journal").start()


_INJECTED = ("<background>", "<retrieved-memory>", "<daily-awareness>", "<tool-images>", "<said-unprompted>",
             "<recently-elsewhere>")
NOTES_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["summary", "open", "facts", "lessons"],
                "properties": {"summary": {"type": "string"},
                               "open": {"type": "array", "items": {"type": "string"}},
                               "facts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                    "required": ["fact", "category", "evidence"],
                                                                    "properties": {"fact": {"type": "string"},
                                                                                   "category": {"type": "string"},
                                                                                   "evidence": {"type": "string"}}}},
                               "lessons": {"type": "array", "items": {"type": "string"}}}}


def their_words(messages: list[dict]) -> list[str]:
    """What the person themself said (not Jarvis's injected notes, tool output or the phone app's channel note)."""
    out = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        parts = [content] if isinstance(content, str) else [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        for text in parts:
            text = re.sub(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*", "", text)   # our own channel note
            if text.strip() and not text.lstrip().startswith(_INJECTED) and not _SECRET.search(text):
                out.append(text.strip())
    return out


def _grounded(sentence: str, said: str, need: int = 2) -> bool:
    """A note the local model wrote is kept only when it's about something they actually said: `need` of its content
    words (or every one, for a short note) occur in their own words. A fact nobody stated is a guess."""
    from core.intelligence import _words
    words = {w for w in _words(sentence, hints=False) if len(w) >= 4}
    theirs = _words(said, hints=False)
    if not words:
        return False
    return len(words & theirs) >= min(need, len(words))


# "my wife Maya", "my sister Marie", "my son Noah": people named in their own words are facts by construction,
# whatever the model's notes keep. A capitalised name right after the relation; nothing inferred.
_RELATION = re.compile(r"\b(?i:my) (?i:(wife|husband|partner|fianc[ée]e?|sister|brother|mother|mom|father|dad|son|"
                       r"daughter|grand(?:mother|father|ma|pa)|aunt|uncle|cousin|niece|nephew|boss|manager|roommate|"
                       r"best friend|friend|dog|cat))(?i:,? (?:whose name is|who is called|called|named|is))? "
                       r"([A-Z][a-z]{2,}(?:[- ][A-Z][a-z]{2,})?)\b")      # the relation any case; the NAME capitalised
_NOT_NAMES = {"And", "But", "The", "Who", "Is", "Was", "Has", "Will", "Can", "Did", "Does", "Lives", "Likes", "Said",
              "Wants", "Also", "Just", "Still", "Always", "Never", "Really", "Probably", "Maybe", "Miami", "December"}


def _stated(said: str) -> list[tuple[str, str]]:
    """(fact, the words of theirs it came from) for each person they named."""
    out = []
    for m in _RELATION.finditer(said):
        relation, name = m.group(1), m.group(2)
        # re.I is for "My wife" at a sentence start; the name itself must still be written with a capital
        if name.split()[0] in _NOT_NAMES or name.split()[-1] in _NOT_NAMES or not name[:1].isupper():
            continue
        fact = f"Their {relation.lower()} is {name}."
        if fact not in [f for f, _ in out]:
            out.append((fact, m.group(0)))
    return out


def stated_relations(said: str) -> list[str]:
    """Facts about people they named ("Their wife is Maya."), from their own words only."""
    return [fact for fact, _ in _stated(said)]


def _plain(text: str) -> str:
    text = str(text).translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})
    return re.sub(r"\s+", " ", text).strip().lower()


# Their correction repeats the invented detail word for word: "Don't say she's getting ready to put the kids in school
# if she didn't tell you that." (2026-10-30). Words they said NOT to say are no evidence for them: a denial anywhere
# earlier in the same clause ("don't say she's getting ready to ..." is three words away), or "you made up that ...".
_DENIAL = re.compile(r"\b(?:don'?t|do not|didn'?t|did not|doesn'?t|does not|never|not|stop|no|made up|make up|making up|"
                     r"invent\w*|wrong|untrue|false|lie|lying)\b")
# A question states nothing ("Did I tell you my wife is pregnant?"), unless it asks Jarvis to keep the words.
# (Speech-to-text often drops the "?": an inverted start counts too. "When it's not morning, ..." is no question.)
_QUESTION = re.compile(r"^\W*(?:(?:and|so|but|also|hey|jarvis|ok|okay)\W+)*(?:(?:did|do|does|is|are|was|were|am|have|has|"
                       r"had|can|could|would|will|should)\s+(?:i|you|we|he|she|they|it|my|your|our|his|her|their|there|"
                       r"that|this)\b|(?:what|who|whose|where|when|why|how|which)(?:'s|\s+(?:is|are|was|were|did|do|does|"
                       r"have|has|had|can|could|would|will|should)\b))")
_KEEP_IT = re.compile(r"\b(?:remember|note|save|keep in mind|write (?:it |that )?down)\b")


def _quoting(evidence: str, lines: list[str]) -> str | None:
    """The sentence of theirs the evidence quotes, or None when it is no statement of theirs (see quoted)."""
    quote = _plain(evidence).strip(" \"'.,!?;:")
    if len(quote.split()) < 2 or len(quote) < 8:
        return None
    for line in lines:
        plain = _plain(line)
        at = plain.find(quote)
        while at >= 0:
            start = max(plain.rfind(c, 0, at) for c in ".!?") + 1
            ends = [i for i in (plain.find(c, at + len(quote)) for c in ".!?") if i >= 0]
            sentence = plain[start:(min(ends) + 1 if ends else len(plain))].strip()
            before = plain[start:at]
            clause = re.split(r"[,;:]|\bbut\b", before)[-1]
            asked = sentence.endswith("?") or _QUESTION.match(sentence)
            if not _DENIAL.search(clause) and not re.search(r"\b(?:made|make|making) up\b", before) \
                    and not (asked and not _KEEP_IT.search(before)):
                return sentence
            at = plain.find(quote, at + 1)
    return None


def quoted(evidence: str, lines: list[str]) -> bool:
    """The evidence is a literal quote of ONE of their own lines (case, spacing and curly quotes aside), at least two
    words long, and a statement of theirs: not words they negated ("don't say ...", "you made up that ..."), and not a
    question ("did I tell you ...?"). Their words only: what Jarvis said is never evidence (a detail he invented and
    said would otherwise quote itself)."""
    return _quoting(evidence, lines) is not None


# The people in the conversation are who a fact is about, never what it says: "Alex prefers Jarvis to stop ending
# answers with questions." quotes "stop ending every answer with a question, I hate that" (live qwen3.5:9b, 3 runs of
# 3 dropped it because "Alex" and "Jarvis" are not in his words).
_PARTICIPANTS = {"jarvis", "owner", "user"}
# The one paraphrase word may not flip how he feels: "likes X" from "I hate X" is an invented fact.
_LIKES = re.compile(r"\b(?:likes?|loves?|enjoys?|fond of|is a fan|favou?rite)\b", re.I)
_DISLIKES = re.compile(r"\b(?:hates?|dislikes?|can'?t stand|cannot stand|annoy\w*|tired of|sick of)\b", re.I)


def _states(fact: str, evidence: str, who: str = "") -> bool:
    """The quote says what the fact says: the fact's content words are its words, but for one (a paraphrase's verb:
    "Likes" for "love"). Two shared words are not enough: "His wife takes their child to school every morning." shares
    "wife" and "child" with "my wife and I, we only have one child" and invents the rest. Who it is about (their
    name, Jarvis) is not content, and the paraphrase may not turn a dislike into a like or back."""
    from core.intelligence import _words
    people = _words(" ".join([who, *_PARTICIPANTS]), hints=False)
    words = {w for w in _words(fact, hints=False) if len(w) >= 4} - people
    if (_LIKES.search(fact) and _DISLIKES.search(evidence) and not _LIKES.search(evidence)) or \
            (_DISLIKES.search(fact) and _LIKES.search(evidence) and not _DISLIKES.search(evidence)):
        return False
    return _grounded(fact, evidence) and len(words - _words(evidence, hints=False)) <= 1


def _evidenced(fact: dict, lines: list[str], who: str = "") -> bool:
    """A fact the local model wrote is kept only with an `evidence` quote of their words that states it, and that did
    not only plan it ("I'm going to live in Denver next month" is no evidence for "They live in Denver.")."""
    from tools import memory
    text, evidence = str(fact.get("fact", "")), str(fact.get("evidence", ""))
    sentence = _quoting(evidence, lines)
    return sentence is not None and _states(text, evidence, who) and not memory.drops_a_plan(text, sentence)


def _local_notes(who: str, transcript_text: str, cancelled=lambda: False, between=lambda: False) -> dict:
    """The journal notes written by the FREE local model as typed JSON; {} when no local tier could. `between`: stop
    before another call (his turn began: core/inferencegate); a call already made still counts."""
    from core import intelligence
    from tools import memory
    prompt = PROMPT.format(who=who, cats=memory.CATEGORIES, transcript=transcript_text)
    prompt = prompt.replace('"humor": list of notes', '"lessons" may be empty. Do not include "humor". Ignore: list of notes')
    schema = {"type": "json_schema", "json_schema": {"name": "jarvis_journal_notes", "strict": True, "schema": NOTES_SCHEMA}}
    # The stronger tier first: the 3B model kept "has a sister named Marie" and dropped "my wife Maya" from the
    # same sentence (1 run in 3). The notes are written off the user's turn (brain._archive), so its load time is free.
    for role in ("advanced", "fast"):
        if cancelled() or between():
            return {}
        try:
            reply = intelligence.request(role, "You write Jarvis's private journal notes as the JSON asked for. No prose.",
                                         [{"role": "user", "content": prompt}], [], cancelled, response_format=schema)
        except intelligence.LocalFailure as error:
            intelligence.audit(role, str(error))
            continue
        text = "".join(b.text for b in reply.content if b.type == "text")
        notes = _notes_object(text)
        if not notes:
            intelligence.audit(role, "invalid_journal_notes")
            continue
        if isinstance(notes, dict) and isinstance(notes.get("summary"), str):
            return notes
    return {}


def archive_local(uid: str, messages: list[dict], wait: float | None = NOTES_WAIT_S):
    """The journal entry, facts and lessons for one finished conversation, written by the local model for free.

    Everything it writes is checked against what the person actually said before it's kept (see _grounded): a fact
    they never stated is dropped, a lesson is kept only when they corrected Jarvis in their own words. The summary
    is marked as the local model's. Without a usable local tier, the entry is the plain record of what they asked
    (never promoted to facts or workflows), as before. Verified PC procedures stay in pcledger, with their gates."""
    topics = their_words(messages)
    if not topics:
        return None
    said = " ".join(topics)
    text, turns = transcript(messages)
    who = users.get(uid).get("name") or "the owner"
    when = datetime.now().isoformat(timespec="minutes")     # when it was archived, not when the card came free
    notes = {}
    if turns >= MIN_USER_TURNS:
        # Background model work (core/inferencegate): after his turn, never during one or a game. Waiting too long
        # (a long game), the entry is still written: the plain record of what was asked, as without a model.
        from core import inferencegate
        with inferencegate.background("journal", timeout=wait) as slot:
            notes = _local_notes(who, text, between=slot.cancelled) if slot else {}
    with users.acting_as(uid):
        summary = str(notes.get("summary", "")).strip() if notes else ""
        if summary and not _SECRET.search(summary) and not _grounded(summary, said):
            summary = ""                                       # a summary of some other conversation: not this one
        entry = {"id": uuid.uuid4().hex[:8], "t": when,
                 "summary": (summary + " (local model's note)") if summary else
                 "Topics requested (outcomes not inferred): " + " / ".join(t[:200] for t in topics[-3:]),
                 "open": [str(o)[:200] for o in _clean(notes.get("open"), 5) if _grounded(str(o), said)] if notes else [],
                 "source": "local_model" if summary else "local_extractive"}
        _append(entry)
        from tools import memory
        for fact, words in _stated(said):                         # people they named: kept whatever the model noted
            memory.remember(fact, "family", False, source="said", evidence=words)
        if notes:
            from core.intelligence import _GROUNDING
            for f in _clean(notes.get("facts")):
                # about what they said AND quoting the words of theirs that state it (a fact nobody said is a guess)
                if isinstance(f, dict) and _grounded(f.get("fact", ""), said) and _evidenced(f, topics, who):
                    cat = f.get("category") if f.get("category") in memory.CATEGORIES else "other"
                    memory.remember(f["fact"], cat, False, source="said", evidence=str(f["evidence"]).strip())
            if _GROUNDING["learn_lesson"].search(said):           # they corrected or complained, in their words
                for lesson in _clean(notes.get("lessons"), 3):
                    if _grounded(str(lesson), said, need=1):      # a rule is a paraphrase: one shared subject word
                        add_lesson(str(lesson), uid)
        return entry


def recent(limit: int = 6) -> list[dict]:
    return entries()[-limit:]


def lessons(limit: int = 25) -> list[str]:
    return [item["lesson"] for item in storage.load(LESSONS, [])[-limit:]]


def search(query: str, limit: int = 5) -> list[dict]:
    """Journal entries about the query, best first: stemmed BM25 with aliases and recency (core/recall.py), over the
    summaries and their "Still open" items, instead of counting raw words."""
    from core import recall
    return [hit.doc.row for hit in recall.search(query, {"journal"})[:limit]]
