"""Which language-teacher skill his words ask for, decided in code before any model chooses (as core/studyintent does
for the study tutor). The same rule governs it: a lesson never makes Jarvis deaf.

  * With no lesson open, only a clear request that names one of the five languages counts: "let's practice Spanish
    for 20 minutes", "teach me Japanese from zero", "how do you say water in French", "my progress in Creole".
    A sentence that merely mentions a language ("is Spanish hard?", "play Spanish music") does not.
  * While a lesson is open, lesson talk is taken ("repeat that", "slower", "skip this word", "end the lesson", "how
    do you say X", "what does X mean"). Skip and stop words that do not name the lesson ("skip", "next", "pass",
    "that's all", "I'm done") are taken only as the reply to a lesson line (lesson["prompted"]): after any other
    turn "next" is for the music. A line is his answer only if it LOOKS like one: it matches or nearly matches what
    was asked, it is written in another script, it is a guess from the course, or it is neither English talk nor
    something the rest of Jarvis would act on. Everything else takes the normal path and the lesson stays open.

call_for() -> (skill name, arguments) or None."""
import re

from core import lang_curriculum as course

_NAMES = {"fr": r"french|fran[cç]ais", "es": r"spanish|espa[nñ]ol|castellano",
          "zh": r"mandarin(?: chinese)?|chinese", "ja": r"japanese|nihongo",
          "ht": r"haitian(?: creole| kreyol| krey[oò]l)?|creole|krey[oò]l(?: ayisyen)?|cr[eé]ole"}
_LANG = "(?P<lang>" + "|".join(_NAMES.values()) + ")"
_ANY = _LANG.replace("?P<lang>", "?:")
_LEAD = (r"^\W*(?:(?:hey|ok|okay|so|now|alright|all right)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?(?:please\s+)?"
         r"(?:(?:can|could|would|will) (?:you|we)\s+(?:please\s+)?|i(?: would|'d) like (?:you )?to\s+|i (?:want|need) (?:you )?to\s+|let'?s\s+|i want\s+|help me\s+)?")
# What may follow the language's name in a request for a lesson ("teach me Spanish cooking" is not one).
_TAIL = (r"(?:\s+(?:lessons?|practice|class|session|vocabulary|vocab|words|phrases|basics|with (?:me|you)|together|again|now|today|tonight|"
         r"please|a (?:bit|little)|some more|for (?:a |an |about |around )?[\w-]+(?: and a half)?[ -](?:minutes?|mins?|hours?|hour)|for (?:a |half an )hour|"
         r"for a (?:bit|while|little while)|from (?:zero|scratch|the (?:very )?(?:beginning|start|basics))|"
         r"(?:like|as if|as though) i(?:'ve| have)? never\b.*|as a (?:complete |total |absolute )?beginner|for (?:complete |total |absolute )?beginners))*\W*$")
_START = re.compile(
    _LEAD + r"(?:(?:practi[cs]e|study|learn|review|revise|do|work on|continue|resume|start|begin|keep (?:practi[cs]ing|learning|going with))\s+"
    r"(?:(?:some|my|a little|a bit of|more|learning|with|on|speaking|our|the)\s+)*|teach me\s+(?:some\s+|a little\s+|to speak\s+)?|"
    r"(?:give|start|begin|have|do|run|continue)\s+(?:me\s+)?(?:a|an|my|another|the|our)\s+(?:\w+[ -]minute\s+)?|"
    r"(?:time for|i'?m ready for)\s+(?:a|an|my|some)?\s*)" + _LANG + _TAIL, re.I)
_MINUTES = re.compile(r"\b(\d{1,3}|a|an|one|two|three|five|ten|fifteen|twenty|twenty[ -]five|thirty|forty|forty[ -]five|sixty)[ -](minutes?|mins?|hours?)\b", re.I)
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30,
          "forty": 40, "forty five": 45, "sixty": 60}
_ZERO = re.compile(r"\bfrom (?:zero|scratch|the (?:very )?(?:beginning|start|basics))\b|\bnever (?:studied|learned|learnt|spoken|taken)\b|"
                   r"\b(?:complete|total|absolute) beginner\b|\bstart(?:ing)? over\b|\bfrom square one\b", re.I)
_PROGRESS = re.compile(
    r"^\W*(?:(?:hey |ok |okay )?jarvis[,.!]?\s+)?(?:(?:what(?:'s| is)|how(?:'s| is)|show me|tell me|give me)\s+my (?:progress|level) (?:in|with|on|for)\s+" + _LANG +
    r"|my (?:progress|level) (?:in|with|on|for)\s+" + _LANG.replace("lang>", "lang2>") +
    r"|how am i doing (?:in|with|on|at)\s+(?:my\s+)?" + _LANG.replace("lang>", "lang3>") +
    r"|how(?:'s| is) my\s+" + _LANG.replace("lang>", "lang4>") + r"(?:\s+(?:going|coming along|coming|doing|progress|progressing))?"
    r"|(?:what|which)\s+" + _LANG.replace("lang>", "lang5>") + r"\s+(?:words|phrases)\s+(?:should i|do i need to|do i have to)\s+(?:practi[cs]e|review|revise)"
    r"|what are my weak (?:spots|points|areas|words) in\s+" + _LANG.replace("lang>", "lang6>") + r")\W*$", re.I)
_PROGRESS_IN = re.compile(r"^\W*(?:what(?:'s| is)|how(?:'s| is)|show me|tell me)?\s*(?:my progress|how am i doing|what should i practi[cs]e|what are my weak (?:spots|points|words))\W*$", re.I)
_SAY = re.compile(r"^\W*(?:(?:hey |ok |okay |and |so )?jarvis[,.!]?\s+)?(?:and\s+)?(?:how (?:do|would|can|does) (?:you|i|one|we|they) say|what(?:'s| is) the (?:word|phrase) for|how to say)\s+"
                  r"(?P<q>.+?)(?:\s+in\s+" + _LANG + r")?\W*$", re.I)
_SAY2 = re.compile(r"^\W*(?:what(?:'s| is)|how(?:'s| is))\s+(?P<q>.+?)\s+in\s+" + _LANG + r"\W*$", re.I)
_SAY3 = re.compile(r"^\W*what(?:'s| is) the\s+" + _LANG + r"\s+(?:word |phrase )?for\s+(?P<q>.+?)\W*$", re.I)
_MEAN = re.compile(r"^\W*(?:(?:hey |ok |okay |and |so )?jarvis[,.!]?\s+)?(?:and\s+)?(?:what does\s+(?P<q>.+?)\s+mean|what(?:'s| is) the meaning of\s+(?P<q2>.+?)|what(?:'s| is)\s+(?P<q3>.+?)\s+mean)"
                   r"(?:\s+in\s+(?:" + _ANY + r"|english))?\W*$", re.I)
_MEAN_IN = re.compile(r"\bin\s+" + _LANG + r"\W*$", re.I)
_STOP = re.compile(r"^\W*(?:(?:hey |ok |okay )?jarvis[,.!]?\s+)?(?:(?:let'?s\s+)?(?:stop|end|finish|quit|exit|cancel)(?: (?:the|this|my|our))? (?:lesson|practice|session|class|" + _ANY + r")(?: lesson| practice)?"
                   r"|(?:let'?s|we can) stop(?: here| there)?|that'?s enough(?: (?:practice|" + _ANY + r"))?|(?:that'?s|that is) all|i'?m done(?: with (?:the |this )?(?:lesson|practice|" + _ANY + r"))?"
                   r"|no more (?:practice|lesson|" + _ANY + r")|(?:stop|end|finish) (?:teaching|practi[cs]ing)(?: me)?)(?: for (?:now|today|tonight))?(?: please| thanks| thank you)?\W*$", re.I)
_SLOWER = re.compile(r"^\W*(?:(?:can|could) you\s+)?(?:please\s+)?(?:(?:say|repeat|speak|do)\s+(?:it|that|this)?\s*(?:again\s+)?(?:but\s+)?(?:a (?:bit|little)\s+)?(?:more\s+)?(?:slower|slowly)|"
                     r"(?:a (?:bit|little)\s+)?(?:more\s+)?(?:slower|slowly)|slow(?: it)? down|(?:that(?:'s| is|was)\s+)?too fast|speak (?:more )?(?:slower|slowly))(?: please)?\W*$", re.I)
_REPEAT = re.compile(r"^\W*(?:(?:can|could) you\s+)?(?:please\s+)?(?:repeat(?: (?:that|it|this|the (?:question|phrase|word)))?|say (?:that|it|this) again|(?:one more|once more)(?: time)?|again|"
                     r"what was (?:that|it|the question)|come again|i didn'?t (?:hear|catch) (?:that|it|you))(?: please)?\W*$", re.I)
_SKIP = re.compile(r"^\W*(?:skip|pass|skip (?:it|this|that)(?: one)?|skip (?:this|that|the) (?:word|phrase|question)|next(?: one| word| phrase| question)?|move on)(?: please)?\W*$", re.I)
# Skip or stop wording that names the lesson; without it the words are his only right after a lesson line.
_NAMED = re.compile(r"\b(?:lessons?|practice|practi[cs]ing|teaching|session|class|word|phrase|question|" + _ANY + r")\b", re.I)
_MAYBE = re.compile(_ANY + r"|how (?:do|would|can|does) \w+ say|what does .+ mean", re.I)
# A line that opens like an order, a question to Jarvis, a greeting or thanks is never an answer by its shape alone.
_NOT_ANSWER = re.compile(
    r"^\W*(?:(?:hey|ok|okay)[,.!]?\s+)?(?:jarvis\b|please\b|stop\b|play\b|pause\b|resume\b|mute\b|unmute\b|open\b|close\b|launch\b|start\b|turn\b|set\b|add\b|"
    r"remove\b|delete\b|send\b|email\b|text\b|call\b|message\b|tell\b|show\b|give\b|find\b|search\b|google\b|look\b|lock\b|unlock\b|shut\b|"
    r"restart\b|remind\b|schedule\b|book\b|buy\b|order\b|volume\b|louder\b|quieter\b|read\b|write\b|save\b|create\b|make\b|run\b|check\b|"
    r"what\b|what'?s\b|who\b|who'?s\b|when\b|where\b|why\b|how\b|can\b|could\b|would\b|will\b|do\b|does\b|did\b|is\b|are\b|am\b|should\b|"
    r"hey\b|hi\b|hello\b|thanks\b|thank\b|good (?:morning|night|evening|afternoon)\b|bye\b|goodbye\b|ok\b|okay\b|never\b|cool\b|nice\b|great\b|"
    r"lol\b|haha\b|yes\b|no\b|yeah\b|nope\b|sure\b|wait\b|hold\b|sorry\b|let'?s\b|i (?:want|need|would like|'d like)\b)", re.I)


def code_of(name: str) -> str:
    """"Kreyol", "haitian creole", "Mandarin", "es" -> the course's code, or ""."""
    low = " ".join(str(name or "").lower().split())
    if low in course.LANGUAGES:
        return low
    return next((code for code, pattern in _NAMES.items() if re.fullmatch(pattern, low, re.I)), "")


def maybe(text: str) -> bool:
    """Could this line be a language request at all, with no lesson open? (One regex.)"""
    return bool(_MAYBE.search(text or ""))


def minutes_in(text: str, default: int = 10) -> int:
    low = text.lower()
    if re.search(r"\bhalf an hour\b", low):
        return 30
    m = _MINUTES.search(low)
    if not m:
        return 60 if re.search(r"\bfor an hour\b", low) else default
    n = _WORDS.get(m.group(1).replace("-", " ")) or (int(m.group(1)) if m.group(1).isdigit() else default)
    return max(2, min(60, n * 60 if m.group(2).startswith("hour") else n))


def _lang_of(match) -> str:
    return next((code_of(v) for k, v in match.groupdict().items() if k.startswith("lang") and v), "")


def _clean(q: str) -> str:
    return " ".join(re.sub(r"^(?:the (?:word|phrase)\s+)", "", (q or "").strip(" \"'“”‘’?.!,"), flags=re.I).strip(" \"'“”‘’").split())


def _request(said: str, lesson: dict | None):
    """A request that is clear with or without a lesson open (a named language), or lesson talk when one is."""
    m = _START.search(said)
    if m and (lesson is None or code_of(m.group("lang")) != lesson["lang"] or _ZERO.search(said) or _MINUTES.search(said)):
        return "language_lesson", {"language": course.LANGUAGES[code_of(m.group("lang"))]["name"], "minutes": minutes_in(said),
                                   "from_zero": bool(_ZERO.search(said))}
    m = _PROGRESS.match(said)
    if m:
        return "language_progress", {"language": course.LANGUAGES[_lang_of(m)]["name"]}
    for pattern in (_SAY3, _SAY, _SAY2):
        m = pattern.match(said)
        if m and _clean(m.group("q")):
            lang = code_of(m.group("lang") or "")
            if pattern is _SAY2 and re.match(r"(?:the|my|your|a|an)\b.*\b(?:weather|time|news|capital|population|price|score)\b", m.group("q"), re.I):
                continue                                         # "what's the weather in French Guiana" is not a translation
            if lang or lesson is not None:
                return "language_say", {"phrase": _clean(m.group("q")), "language": course.LANGUAGES[lang]["name"] if lang else ""}
    m = _MEAN.match(said)
    if m:
        q = _clean(m.group("q") or m.group("q2") or m.group("q3"))
        named = _MEAN_IN.search(said)
        if q and (lesson is not None or named) and q.lower() not in ("it", "that", "this", "life"):
            return "language_meaning", {"phrase": q, "language": course.LANGUAGES[code_of(named.group("lang"))]["name"] if named else ""}
    return None


def looks_like_answer(said: str, lesson: dict, orders=None) -> bool:
    """Is this line his answer to the step on the table? lesson: {"kind": "say"|"repeat"|"meaning"|"talk", "check":
    said -> verdict, "guess": said -> is it a meaning from the course, "fresh": asked moments ago}. orders(said):
    would the rest of Jarvis act on it, or is it small talk."""
    verdict = lesson["check"](said) if lesson.get("check") else {}
    if verdict.get("correct") or verdict.get("near"):
        return True                                              # right, or nearly: an answer whatever else it looks like
    if lesson["kind"] == "meaning":
        if lesson.get("guess") and lesson["guess"](said):
            return True                                          # a wrong guess, but a guess: "good night"
        if "?" in said or _NOT_ANSWER.match(said) or (orders and orders(said)):
            return False
        return bool(lesson.get("fresh")) and len(said.split()) <= 6
    if any(ord(c) >= 0x2E80 for c in said):
        return True                                              # kana or characters: he is speaking the language
    if orders and orders(said):
        return False
    share = _english(said)
    no_something = len(said.split()) == 2 and said.lower().startswith("no ") and share <= 0.5      # "no sé", "no entiendo": a try
    if share > 0.5 or (_NOT_ANSWER.match(said) and not no_something):
        return False                                             # English talk: "no", "hello", "pause the music", "what time is it"
    return True


def _english(said: str) -> float:
    """The share of common English words: high means talk to Jarvis, not an attempt at the language."""
    try:
        from audio import language
        words = language._words(said)
        return sum(w in language._ENGLISH for w in words) / len(words) if words else 0.0
    except Exception:                                            # noqa: BLE001
        return 0.0


def call_for(text: str, lesson: dict | None = None, orders=None):
    """lesson: tools.langlesson.on_the_table() (None when no voice/chat lesson is open), with "prompted": the
    turn before this one was the lesson's own (the Brain knows; absent means no)."""
    said = " ".join((text or "").split())
    if not said or len(said) > 400:
        return None
    if lesson is None:
        return _request(said, None) if maybe(said) else None
    ours = bool(lesson.get("prompted")) or bool(_NAMED.search(said))
    if _STOP.match(said):
        return ("language_stop", {}) if ours else None
    if _SLOWER.match(said):
        return "language_repeat", {"slower": True}
    if _REPEAT.match(said):
        return "language_repeat", {"slower": False}
    if _SKIP.match(said):
        return ("language_skip", {}) if ours else None
    if _PROGRESS_IN.match(said):
        return "language_progress", {"language": ""}
    found = _request(said, lesson)
    if found:
        return found
    from core import lang_engine
    if lang_engine.gave_up(said) or looks_like_answer(said, lesson, orders):
        return "language_answer", {"answer": said}
    return None
