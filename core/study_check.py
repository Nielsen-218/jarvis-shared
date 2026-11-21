"""A referee for one rung of the explanation ladder: is this text really that rung, for this topic, one step below the
last one?

The local model often writes the first sentence of the source for every rung, or the same story twice, or a "pre-K"
explanation as long as a college one. judge() says why a draft is not acceptable, in a sentence the model can act on,
and the engine asks once more with that reason. Pure: no model, no storage, no clock.

    judge(rung, text, topic=, source=, analogy=, previous=) -> None (fine) or (code, reason)
    codes: empty, no_topic, long, copy, repeat, harder, thin, comparison, no_example, off_topic, no_analogy, jargon
    HARD: the codes that a second failed attempt must not be shown for (the engine falls back to the material instead)."""
import re

from core import study_ladder, study_store
from core.words import plural

# (most sentences, most words) a rung may use when spoken aloud; a little above what the prompt asks for.
LIMITS = {"college": (7, 170), "high": (7, 170), "middle": (5, 110), "elementary": (5, 85), "prek": (3, 32), "one": (2, 12)}
# (sentences, words) a rung may never exceed, whatever the model wrote: enforced by cap() on every ladder reply.
NEEDS_ANALOGY = ("middle", "elementary", "prek")
HARD = ("empty", "copy", "repeat", "off_topic", "no_topic")
_PAREN = re.compile(r"\([^)]*\)")
_COMPARISON = re.compile(r"\b(?:is|are|works?|acts?|behaves?) (?:just )?like (?:a|an|the|your|my)\b|\bjust like\b|\bimagine (?:a|an|the|you|that)\b[^.]{0,60}\blike\b", re.I)
_CUE = re.compile(r"\b(?:like|imagine|pretend|picture|once|think of|as if|story|same way|just as)\b", re.I)
_EXAMPLE = re.compile(r"\d|\bfor (?:example|instance)\b|\bexample:", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in study_ladder._SPLIT.split(" ".join(str(text or "").split())) if s.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", _PAREN.sub(" ", text or ""))


def _syllables(word: str) -> int:
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    n = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def grade(text: str) -> float:
    """Flesch-Kincaid grade level: how much schooling the wording asks for."""
    ws = re.findall(r"[A-Za-z']+", text or "")
    if not ws:
        return 0.0
    return 0.39 * len(ws) / max(1, len(sentences(text))) + 11.8 * sum(_syllables(w) for w in ws) / len(ws) - 15.59


def echo(a: str, b: str) -> float:
    """How much two texts are the same words (Jaccard over stemmed content words), 0..1."""
    x, y = set(study_store.tokens(a)), set(study_store.tokens(b))
    return len(x & y) / len(x | y) if x and y else 0.0


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _contained(text: str, other: str) -> float:
    x, y = set(study_store.tokens(text)), set(study_store.tokens(other))
    return len(x & y) / len(x) if x else 0.0


def _vocabulary(topic: str, source: str) -> set[str]:
    return set(study_store.tokens(topic)) | set(study_store.tokens(source))


def has_figures(text: str, anchors) -> bool:
    """At least half of the computed figures (an anchor may list alternative spellings with |) are in the text."""
    low = (text or "").lower()
    found = sum(any(alt.lower() in low for alt in a.split("|")) for a in anchors)
    return found >= max(1, (len(anchors) + 1) // 2)


def _comparison_in(text: str, analogy: str) -> bool:
    """Does the explanation really use the everyday comparison it names (or carry a story / comparison cue)?"""
    if not _PAREN.sub("", analogy or "").strip():
        return False
    mine = {t for t in study_store.tokens(analogy) if len(t) >= 4}
    return bool(mine & set(study_store.tokens(text))) or bool(_CUE.search(text))


def cap(rung: str, text: str) -> str:
    """The text cut to whole sentences until it is within what the rung allows (sentences and words, LIMITS). Always keeps
    the first sentence, and cuts a lone over-long sentence at a clause or at the word limit."""
    rung = rung if rung in study_ladder.RUNGS else study_ladder.START
    most_sents, most_words = LIMITS[rung]
    text = " ".join(str(text or "").split())
    parts = sentences(text)
    keep = []
    for part in parts:
        if len(keep) >= most_sents or (keep and len(words(" ".join(keep + [part]))) > most_words):
            break
        keep.append(part)
    out = " ".join(keep)
    if len(words(out)) > most_words:
        ws = out.split()
        cut = " ".join(ws[:most_words])
        clause = [m.start() for m in re.finditer(r",|;| but | and | so ", cut) if len(cut[:m.start()].split()) >= 4]
        out = (cut[:clause[-1]] if clause else cut).rstrip(" ,;") + "."
    return out


def shorter(rung: str, text: str, previous: dict | None) -> str:
    """Going DOWN the ladder from `previous`, a reply for the middle rung and below is shorter than the one above it (fewer
    words), cut by whole sentences; the college and high rungs only have to stay within their limits."""
    if not previous or not previous.get("text") or rung not in ("middle", "elementary", "prek", "one"):
        return text
    was = previous.get("level")
    if was not in study_ladder.RUNGS or study_ladder.RUNGS.index(was) >= study_ladder.RUNGS.index(rung):
        return text
    before = len(words(previous["text"]))
    parts = sentences(text)
    while len(parts) > 1 and len(words(" ".join(parts))) >= before:
        parts.pop()
    return " ".join(parts)


# Verbs that are only ever a gesture when they open a parenthesis, and verbs that are ordinary words ("open source", "use case",
# "close to") and so count as a gesture only when bare (alone, or with an adverb) or aimed at the listener ("show your hands").
_GESTURE = re.compile(r"^(?:point|wave|clap|nod|smile|hug|tap|knock|bounce|jump|shake|pat|touch|reach|stomp|cheer|giggle|laugh|gasp|"
                      r"pause|pretend|mime|whisper|sing|dance|spin|peek|hide|rub|squeeze|bow|beckon|gesture|wink|grin|sigh)(?:s|ing|es)?\b"
                      r"(?!\s+(?:of|out of)\b)", re.I)
_VERB = re.compile(r"^(?:show|hold|give|hand|push|pull|roll|make|open|close|lift|raise|look|sound|use)(?:s|ing)?\b", re.I)
_BARE = {"up", "down", "twice", "once", "slowly", "again", "out", "now", "quickly", "softly", "gently", "here", "there", "back",
         "around", "away", "over", "it", "one", "two", "three", "wide", "high", "low", "closely", "happy", "excited", "surprised"}
_AIMED = {"your", "you", "me", "my", "yourself"}
_SMALL = ("elementary", "prek", "one")
_MARK = "\x00"
_BOLD = re.compile(r"\*\*([^*\n]{1,120})\*\*")
_ASTER = re.compile(r"\*([^*\n]{1,80})\*")
_SQUARE = re.compile(r"\[([^\[\]\n]{0,80})\]")


def is_gesture(inner: str) -> bool:
    """A stage direction ("point to the door", "clap twice", "show your hands", "pause"), not a definition, an acronym's
    expansion, a citation or an index ("open source code", "Open Systems Interconnection", "a /26 mask", "1", "i")."""
    inner = (inner or "").strip()
    ws = inner.split()
    if not ws or len(ws) > 8 or not inner[0].islower():
        return False
    if _GESTURE.match(inner):
        return True
    if _VERB.match(inner):
        rest = {w.lower().strip(".,!") for w in ws[1:]}
        return not rest or rest <= _BARE or bool(rest & _AIMED)
    return False


def _direction(match, rung: str) -> str:
    if rung in _SMALL or is_gesture(match.group(1)):
        return _MARK
    return match.group(0)


def spoken(rung: str, text: str) -> str:
    """The text with every stage direction taken out: "(point)", "(clap twice)", "[waves]", "*smiles*". A tutor speaks, it does
    not describe a gesture. The three lowest rungs lose every parenthesis, bracket and asterisk pair; the upper rungs only the
    ones that are a gesture (a "(RAM)", "(open source code)", "[1]" or "2 * 3 * 4" in a textbook sentence stays). Markdown bold is
    unwrapped everywhere (the word stays). The citation is added after this, by the skill, and stays."""
    text = str(text or "")
    out = _BOLD.sub(r"\1", text)
    out = re.sub(r"\(([^()]*)\)", lambda m: _direction(m, rung), out)
    out = _SQUARE.sub(lambda m: _direction(m, rung), out)
    out = _ASTER.sub(lambda m: _direction(m, rung), out)
    if _MARK not in out:
        return " ".join(out.split())

    def close(m):
        before, after = m.group(1), m.group(2)
        if not after:
            return before + " "
        if before:
            return before if set(after) <= set(".,") else before + after
        return after
    out = re.sub(r"([!?.]?)\s*" + _MARK + r"\s*([.,;:!?]*)", close, out)
    return " ".join(out.split())


def stop(text: str) -> str:
    """The sentence with a full stop when it has no ending mark, so a sentence added after it starts cleanly."""
    text = " ".join(str(text or "").split())
    return text if not text or text[-1] in ".!?" else text + "."


def _same_word(a: str, b: str) -> bool:
    """Two stems that are one word in another form (subnett / subnet): one starts with the other, both long enough."""
    return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))


def _acronym_said(text: str, topic: str) -> bool:
    """The topic is an acronym (CPU, RAM) and the text spells it out: consecutive words that start with its letters."""
    letters = (topic or "").strip()
    if not (2 <= len(letters) <= 6 and letters.isalpha() and letters.isupper()):
        return False
    ws = re.findall(r"[A-Za-z]+", text or "")
    n = len(letters)
    return any("".join(w[0] for w in ws[i:i + n]).upper() == letters for i in range(len(ws) - n + 1))


def names(text: str, topic: str) -> bool:
    """Does the explanation say what it is about? At least half of the topic's own words (one word of two, two of three), in
    any form of the word, or the expansion of an acronym topic."""
    want = set(study_store.tokens(topic))
    if not want:
        return True
    have = set(study_store.tokens(text))
    if sum(any(_same_word(w, h) for h in have) for w in want) >= (len(want) + 1) // 2:
        return True
    return _acronym_said(text, topic)


def related(text: str, topic: str, source: str) -> bool:
    """A draft that does not use the topic's name but is plainly about it: at least three of its words are the material's own."""
    if not (source or "").strip():
        return False
    return len(set(study_store.tokens(text)) & _vocabulary(topic, source)) >= 3


def uses_comparison(text: str, analogy: str, topic: str = "") -> bool:
    """Is the everyday comparison the model named really in the spoken text? Said before "where the comparison stops working"."""
    mine = {t for t in study_store.tokens(analogy) if len(t) >= 3} - set(study_store.tokens(topic))
    return bool(mine & set(study_store.tokens(text)))


def unnamed(rung: str, text: str, topic: str):
    """("no_topic", reason) when the explanation never names its topic (the engine asks again once, then keeps a draft that does)."""
    if topic and text and not names(spoken(rung, text), topic):
        return "no_topic", f"It never says what it is about. Name {topic} in the explanation, in the first sentence."
    return None


def lead_with_topic(rung: str, text: str, topic: str) -> str:
    """Finalize, but when the cut loses the only sentence that names the topic, that sentence goes first."""
    final = finalize(rung, text)
    if names(final, topic) or not names(spoken(rung, text), topic):
        return final
    parts = sentences(spoken(rung, text))
    named = next((p for p in parts if names(p, topic)), "")
    return finalize(rung, " ".join([named] + [p for p in parts if p is not named])) if named else final


def finalize(rung: str, text: str) -> str:
    """Every rung held to its hard limit (cap) with no stage direction in it (spoken); the one-year-old a handful of words
    (at most 12) in at most two sentences, ending with a cheer."""
    text = spoken(rung, " ".join(str(text or "").split()))
    if rung != "one" or not text:
        return cap(rung, text) if text else text
    plain = text
    out, used = [], 0
    for sent in sentences(plain):
        ws = sent.split()
        if used + len(ws) > LIMITS["one"][1] or len(out) >= LIMITS["one"][0]:
            if not out:
                cut = " ".join(ws[:LIMITS["one"][1]])
                clause = [m.start() for m in re.finditer(r",| but | and | so |;", cut) if len(cut[:m.start()].split()) >= 3]
                out.append(cut[:clause[0]] if clause else cut)           # a clause that stands alone, not a sentence cut mid-way
            break
        out.append(sent)
        used += len(ws)
    body = " ".join(out) if out else plain.strip()
    body = body.rstrip(" .?") if body[-1:] != "!" else body
    return body if body.endswith("!") else body + "!"


def jargon(text: str, topic: str, source: str) -> list[str]:
    """Long technical words of the source (not the topic's own name) that a text uses."""
    own = set(study_store.tokens(topic))
    source_words = {w for w in re.findall(r"[a-z][a-z-]{7,}", (source or "").lower())}
    seen = []
    for w in re.findall(r"[a-z][a-z-]{7,}", (text or "").lower()):
        if w in source_words and study_store.tokens(w)[:1] and study_store.tokens(w)[0] not in own and w not in seen:
            seen.append(w)
    return seen


def judge(rung: str, text: str, topic: str = "", source: str = "", analogy: str = "", previous: dict | None = None, anchors=()):
    """None when the draft is a fair explanation at `rung`; else (code, reason), the reason written to the model.
    previous: {"level", "text"} of the explanation he had just before (the rung above when he asked for simpler)."""
    rung = rung if rung in study_ladder.RUNGS else study_ladder.START
    text = " ".join(str(text or "").split())
    if not text:
        return "empty", "You wrote no explanation."
    sents, count = len(sentences(_PAREN.sub(" ", text) if rung == "one" else text)), len(words(text))
    most_sents, most_words = LIMITS[rung]
    parts = sentences(text)
    if source and len(set(study_store.tokens(text))) >= 6 and len(parts) <= 2 and any(
            len(set(study_store.tokens(s))) >= 6 and _contained(text, s) >= 0.85 for s in sentences(source)):
        return "copy", ("That is the source's own sentence again, not an explanation. Say it in your own words, "
                        "and show how it works rather than what it is called.")
    if len(parts) >= 3 and source and sum(len(_norm(s).split()) >= 5 and _norm(s) in _norm(source) for s in parts) / len(parts) >= 0.6:
        return "copy", ("Most of it is the source's own sentences word for word. Say it in your own words and show how it works, "
                        "do not read the source back.")
    if topic and study_ladder.EXAMPLE_TOPIC not in topic.lower() and "cache" not in topic.lower() and rung in study_ladder.EXAMPLE:
        shared = set(study_store.tokens(text)) & set(study_store.tokens(study_ladder.EXAMPLE[rung]))
        if len(shared) >= 4 and _contained(study_ladder.EXAMPLE[rung], text) >= 0.5:
            return "copy", (f"That is the wording of the example in the prompt (a different topic), not an explanation of {topic}. "
                            f"Explain {topic} in new words and name it.")
    if previous and previous.get("text"):
        if len(set(study_store.tokens(text))) >= 8 and echo(text, previous["text"]) >= 0.7:
            return "repeat", "It repeats the previous explanation. Use a different example or comparison and different sentences."
        before = previous.get("level")
        if before in study_ladder.RUNGS and study_ladder.RUNGS.index(before) < study_ladder.RUNGS.index(rung):
            if rung in ("prek", "one"):
                if count >= len(words(previous["text"])):
                    return "harder", "It is not simpler than the previous one: it is as long. Say less, with smaller words."
            elif grade(text) > grade(previous["text"]) + 1.5:
                return "harder", ("It is not simpler than the previous explanation: the sentences and the words are as hard or "
                                  "harder. Use shorter sentences and everyday words, one idea at a time.")
    if sents > most_sents or count > most_words:
        return "long", (f"It is too long to say aloud at this level ({plural(count, 'word')}, {plural(sents, 'sentence')}). "
                        f"Use at most {plural(most_sents - 1, 'sentence')} and about {plural(most_words * 3 // 4, 'word')}.")
    if rung in ("college", "high"):
        if len(parts) < 3 or count < (35 if rung == "college" else 30):
            return "thin", ("It is too thin for this level. Say how it works step by step, in at least 3 sentences, "
                            "with the real terms" + (" and one worked example with concrete values." if rung == "high" else "."))
        if _COMPARISON.search(text):
            return "comparison", ("It uses an everyday comparison, which this level does not. Explain the mechanism itself"
                                  + (", with one worked example that has concrete values." if rung == "high" else "."))
        if anchors and not has_figures(text, anchors):
            return "numbers", ("It does not use the computed figures. Work the example with exactly these numbers: "
                               + ", ".join(a.split("|")[0] for a in anchors) + ".")
        if rung == "high" and not _EXAMPLE.search(text):
            return "no_example", "It has no worked example. Walk through ONE example with concrete values (a real number, name or address)."
    vocab = _vocabulary(topic, source)
    hits = len(set(study_store.tokens(text)) & vocab)
    need = min(2 if rung in ("college", "high") else 1, len(vocab))
    if source.strip() and hits < need and not (rung in ("prek", "one") and analogy) and not (rung == "elementary" and _comparison_in(text, analogy)) and vocab:
        return "off_topic", f"It does not explain {topic or 'the topic'}. Stay with the same idea and name it."
    if rung in NEEDS_ANALOGY and not _comparison_in(text, analogy):
        return "no_analogy", ("It has no everyday comparison in it. Compare the idea to one real thing from daily life that works the "
                              "same way, and say the comparison in the explanation itself.")
    if rung in ("prek", "one"):
        technical = jargon(text, topic, source)
        if len(technical) >= 1 and rung == "one" or len(technical) >= 2:
            return "jargon", f"It uses grown-up words ({', '.join(technical[:3])}). Only words a small child knows."
    return None
