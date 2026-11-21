"""Is what the mic just heard Jarvis's OWN voice coming back from the speakers?

Owner 2026-11-07: "He picks up his own voice when he speaks ... I want to be able to use him without headphones." The level test
in audio.listener (louder than the expected echo) lets loud stretches of his voice through on speakers, and the wake word fires
when he says "Jarvis". So before anything heard over or right after his speech is acted on, its words are compared with the
sentences he just played: words he said himself, in his order, are echo and are ignored. Nothing here can approve a plan:
a "go ahead" that is his own words never reaches the brain.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, List, Tuple

_COMMANDS = {"stop", "wait", "cancel", "quiet", "enough", "shush", "shh", "pause", "no", "nope", "hold"}
_WAKE = re.compile(r"^(?:(?:hey|hi|ok|okay|yo)\s+)?(?:jarvis|jervis|travis|service)\b\s*", re.I)


_SENTENCE = re.compile(r"\S.*?(?:[.?!]+(?=\s|$)|$)", re.S)   # ends at . ? ! before a space: "3.5", "google.com" stay whole
_WORD = re.compile(r"[a-z0-9']+", re.I)        # _words() on the original text, with where each word is
# A sentence this long that he said word for word, in his order, is his even when it is not marked by "sir" (an owner
# sentence of three words or fewer that happens to be in his reply, "Streaming protocol.", "Open World Intelligence.", is
# kept). Only a sentence of SLIP_RUN words or more may differ from his by speech-to-text slips: an owner's answer of four
# to six words that reuses his question's words ("...or a new one I should create from scratch?" "Create a new one from
# scratch.", data/jarvis.log 2026-11-15) is not his.
LONG_RUN = 4
SLIP_RUN = 7
ALIGNED = 0.8          # share of a sentence's words found in runs of 2+ of his words, in a stretch his length (STT slips)


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", (text or "").casefold().replace("’", "'"))


def _contains(seq: List[str], sub: List[str]) -> bool:
    n = len(sub)
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def is_echo(heard: str, spoken: Iterable[str]) -> bool:
    """True when `heard` is (a stretch of) what Jarvis just said, allowing for speech-to-text slips. Empty heard is echo
    too (a level spike with no words). A sentence of the owner's own, even one that shares words with his, is not."""
    words = _words(heard)
    said = _words(" ".join(spoken))
    if not words:
        return True
    if not said:
        return False
    if any(w in _COMMANDS and w not in said for w in words):
        return False                                 # the owner's "stop" over his voice always gets through
    if _contains(said, words):                       # word for word, in his order: "go ahead", "sir"
        return True
    rest = _words(_WAKE.sub("", heard.strip()))
    if rest != words and rest and _contains(said, rest):
        return True                                  # "Jarvis, go ahead" heard while he said "...want me to go ahead"
    if len(words) >= 3:                              # speech-to-text slips: most words his, mostly in order
        known = set(said)
        mine = sum(w in known for w in words) / len(words)
        pairs = list(zip(words, words[1:]))
        said_pairs = set(zip(said, said[1:]))
        ordered = sum(p in said_pairs for p in pairs) / len(pairs)
        return mine >= 0.8 and ordered >= 0.5
    return False


def _filler(words: List[str]) -> bool:
    """His courtesy words: "sir", or a short run ending in it ("Understood, sir.", "Yes, sir.", "As I said, sir")."""
    return bool(words) and words[-1] == "sir" and len(words) <= 5


def _aligned(words: List[str], said: List[str]) -> float:
    """How much of `words` lines up, in runs of two or more, with one stretch of `said` about its own length."""
    n, best = len(words), 0
    first = set(words)
    for j, w in enumerate(said):
        if w not in first:
            continue
        blocks = SequenceMatcher(None, words, said[j:j + n + 2], autojunk=False).get_matching_blocks()
        best = max(best, sum(b.size for b in blocks if b.size >= 2))
    return best / n if n else 0.0


def _his_sentence(words: List[str], said: List[str], long_runs: bool = True) -> bool:
    """One sentence of what was heard is Jarvis's: his courtesy run, or (long_runs) 4+ of his words in his order, word
    for word, or with speech-to-text slips from 7 words on."""
    if not words or any(w in _COMMANDS for w in words):
        return False                                 # "No, sir." is the owner's even if Jarvis also said "no, sir"
    if _filler(words):
        return _contains(said, words)
    if not long_runs or len(words) < LONG_RUN:
        return False                                 # "Streaming protocol." is the owner's, even if Jarvis said it
    return _contains(said, words) or (len(words) >= SLIP_RUN and _aligned(words, said) >= ALIGNED)


def _edge_run(words: List[str], said: List[str], at_end: bool) -> int:
    """Words at the start (or end) of a sentence that are his courtesy run ("yes sir" in "Yes sir, all right"): how many."""
    for k in range(min(len(words) - 1, 5), 1, -1):
        run = words[-k:] if at_end else words[:k]
        if run[-1] == "sir" and _contains(said, run) and not any(w in _COMMANDS for w in run):
            return k
    return 0


def subtract(heard: str, spoken: Iterable[str], long_runs: bool = True) -> str:
    """`heard` with Jarvis's own words taken out: what the owner said. "" when nothing of his is left.

    Over his voice on speakers the mic hears both, and Whisper writes both into one line (data/jarvis.log 2026-11-20:
    "Go live. Understood, sir." while he said "Understood, sir."; "...did Apex get a new update? As I said, sir."). is_echo
    is all or nothing, so such a line went to the brain whole. Here, sentence by sentence (Whisper's own punctuation), a
    sentence that is his is dropped: his courtesy run ("Understood, sir.", "Yes, sir.") or four or more of his words in
    his order. A short sentence of the owner's that shares words with his reply ("Streaming protocol.") is kept, so is
    anything with a stop word he did not say ("No." / "Stop." / "Wait."). At the edges of what is left, a courtesy run
    glued to the owner's words ("Yes, sir, tell me the weather") is trimmed too.

    long_runs=False takes out only his courtesy runs: after he stopped talking the mic can carry no more than the last
    moment of his voice or a "Yes, sir?" just said, while an owner who answers at once often reuses his words."""
    said = _words(" ".join(spoken))
    if not said or not (heard or "").strip():
        return (heard or "").strip()
    text = heard.replace("’", "'")             # same length: spans found here cut `heard` as written
    pieces = [(m.start(), m.end()) for m in _SENTENCE.finditer(text) if _words(m.group())]
    kept = [(a, b) for a, b in pieces if not _his_sentence(_words(text[a:b]), said, long_runs)]
    if not kept:
        return ""
    changed = len(kept) != len(pieces)
    for index, at_end in ((0, False), (-1, True)):
        a, b = kept[index]
        spans = [(m.start() + a, m.end() + a) for m in _WORD.finditer(text[a:b])]
        k = _edge_run([text[x:y].casefold() for x, y in spans], said, at_end)
        if k:
            a, b = (a, spans[-k - 1][1]) if at_end else (spans[k][0], b)
            kept[index] = (a, b)
            changed = True
    if not changed:
        return heard.strip()
    out = " ".join(heard[a:b].strip() for a, b in kept)
    out = re.sub(r"^[\s,;:.\-]+", "", out).strip()
    if out and out[0].islower() and kept[0][0] > 0:
        out = out[0].upper() + out[1:]
    return out if _words(out) else ""


def context(speaker) -> Tuple[List[str], List[str]]:
    """What the mic may be hearing of Jarvis himself: (this reply's sentences, audio.tts.Speaker.spoken; other sentences
    he played in the last seconds, Speaker.recently_played: a "Yes, sir?" just before this reply). A speaker without
    them (a test double) has none."""
    spoken = getattr(speaker, "spoken", None)
    spoken = list(spoken) if isinstance(spoken, (list, tuple)) else []
    recent = getattr(speaker, "recently_played", None)
    earlier = recent() if callable(recent) else None
    earlier = [t for t in earlier if t not in spoken] if isinstance(earlier, (list, tuple)) else []
    return spoken, earlier


def owner_part(heard: str, spoken: Iterable[str], earlier: Iterable[str] = (), long_runs: bool = True) -> str:
    """What of `heard` is the owner's: subtract() his sentences, then "" if what is left is still his (is_echo: a fragment
    like "go ahead", or a whole line of his that Whisper garbled). A line is_echo judged his because MOST of its words are
    his keeps the owner's part when that part has a word he did not say ("Open Blender. As I said, sir." -> "Open
    Blender."); with none ("Go ahead. As I said, sir.") it stays echo, so his own words still never approve a plan.

    `earlier` (sentences of an earlier reply still in the air) is only subtracted, never used for is_echo: the owner's
    one-word "Yes." over this reply is his answer even when the "Yes, sir?" before it was Jarvis's."""
    spoken = list(spoken)
    if not _words(heard):
        return ""
    kept = subtract(heard, spoken + [t for t in earlier if t not in spoken], long_runs)
    if not kept or is_echo(kept, spoken):
        return ""
    if is_echo(heard, spoken) and set(_words(kept)) <= set(_words(" ".join(spoken))):
        return ""
    return kept


def without_mine(text: str, mine: Iterable[str], earlier: Iterable[str] = (), long_runs: bool = True) -> str:
    """The voice loop's use of owner_part(): `text` without his words, logged; "" when none of it was the owner's."""
    mine, earlier = list(mine), list(earlier)
    if not (mine or earlier) or not (text or "").strip():
        return text
    kept = owner_part(text, mine, earlier, long_runs)
    if not kept:
        print(f"  (ignored my own voice: {text!r})")   # the tail of his reply, heard as a command
    elif kept != text.strip():
        print(f"  (removed my own words: {text!r} -> {kept!r})")
    return kept
