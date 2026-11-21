"""Which study skill his words ask for, decided in code before any model chooses (as core/webintent does for the
web). The rule that governs everything here: study must never make Jarvis deaf or take over ordinary talk. When a
line is not clearly a study request, it is NOT one, and the normal conversation handles it.

  * With no quiz open, only an instruction addressed to Jarvis about studying counts: "quiz me on chapter 3", "test me
    on networking", "make flashcards for chapter 2", "review my flashcards", "what should I study", "explain
    subnetting like I'm five". A sentence that merely contains "quiz", "test", "cards" or "exam" does not.
  * While a quiz question (started by voice or chat) is on the table, a line is his answer only if it LOOKS like one:
    for multiple choice a letter, "the second one" or an option's words; for a written question a statement with no
    command, question to Jarvis, greeting or thanks in it. Everything else leaves the quiz open and goes down the
    normal path, unrecorded.
  * A follow-up ("explain it another way", "I still don't understand", "simpler", "more detail", "like I'm in
    middle school") is taken only in the turn or two right after a study reply; a bare "explain it" only in the very
    next one. Outside that window "more detail on the invoice" or "simpler" is ordinary talk.
  * The explanation ladder (core/study_ladder): "simpler" and "like I'm five" mean ONE rung down, "more detail" one
    rung up, and a rung he names ("like I'm one") is that rung.
  * The whiteboard: "show me DNS on the whiteboard" or "draw subnetting" at any time, but only for a topic that is
    in his study material ("draw a bath" and "draw the curtains" are not), and a bare "draw a ..." only while studying.

call_for() -> (skill name, arguments) or None."""
import re
from datetime import date, timedelta

from core import study_ladder as ladder

# A line may start with a call to Jarvis and a polite lead-in; the instruction itself must come right after it.
_LEAD = (r"^\W*(?:(?:hey|ok|okay|so|now|alright|all right)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?(?:please\s+)?"
         r"(?:(?:can|could|would|will) you\s+(?:please\s+)?|i(?: would|'d) like (?:you )?to\s+|i (?:want|need) (?:you )?to\s+|let'?s\s+|i want\s+)?")
_CHAPTER = re.compile(r"\b(?:chapter|module|unit|lecture|week|lesson|part)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", re.I)
_COUNT = re.compile(r"\b(\d{1,2}|five|ten|fifteen|twenty)\s+(?:\w+\s+)?questions?\b", re.I)
_NUMBERS = {"five": 5, "ten": 10, "fifteen": 15, "twenty": 20}
# Cheap first look: no study wording at all means the store is never touched for this line.
_MAYBE = re.compile(r"quiz|flash ?card|\bcards?\b|\bstud(?:y|ies|ying)\b|\bexam|\btest me\b|\bdrill me\b|explain|teach me|\bchapter\b|"
                    r"\bcourses?\b|\bnotes\b|textbook|\bmidterm\b|\bfinal\b|another way|understand|knowledge gaps?|white ?board|\bdraw\b|\bsketch\b|on the board\b|\bweak\b|\breview\b|practice (?:exam|test)|mock exam|\bhow am i doing\b", re.I)
_QUIZ = re.compile(_LEAD + r"(?:(?:quiz|test|drill) me\b|(?:give me|start|run|do|have|begin)\s+(?:a|an|another|my)\s+(?:\w+\s+)?(?:quiz|practice (?:exam|test|quiz)|mock exam)\b)", re.I)
# After "give me a quiz": nothing, or what it is on. ("...a quiz for my kids' party" is not his studying.)
_QUIZ_TAIL = re.compile(r"\W*$|\s*[,.]?\s*(?:please\W*$|(?:on|about|from|over|with|of|in)\b|for\s+(?:my |the )?(?:chapter|course|class|exam|test|final|midterm)\b)", re.I)
_EXAM = re.compile(r"\b(?:practice|mock) (?:exam|test)\b", re.I)
_FROM_MINE = re.compile(r"\bfrom (?:my|the) (?:notes|textbook|book|slides|course|class|material|chapter)\b", re.I)
_EXPLAIN = re.compile(_LEAD + r"(?:explain|teach me(?: about)?|walk me through|help me understand)\b", re.I)
_REFERS = re.compile(r"\b(?:th(?:e|at|is) (?:question|answer|card|one)|the last (?:question|one))\b", re.I)
# "Explain it another way" and its kin: always about the explanation or question just given.
_AGAIN = re.compile(r"\b(?:explain|say|put|describe|show)\b.{0,20}\b(?:another|a different|some other|a new) (?:way|example|analogy|angle)\b|"
                    r"^\W*(?:try |use |give me |how about |what about )?(?:another|a different) (?:way|example|analogy)(?: please)?\W*$|"
                    r"\bexplain (?:it|that|this) (?:again|differently|more simply|simpler)\b", re.I)
# "I still don't get it": the explanation was too hard, so one rung down the ladder.
_STILL = re.compile(r"\bi still (?:do not|don'?t|dont) (?:understand|get it|get that|follow)\b", re.I)
# A line that is nothing but a move on the ladder, once its level words are taken out ("ok, simpler please",
# "now explain it like I'm one", "in more detail"). Only read in the turn or two after a study reply.
_ONLY_FILLER = re.compile(r"^(?:\W|\b(?:ok|okay|now|and|then|so|but|please|jarvis|can|could|would|you|explain|say|put|make|try|give|me|it|"
                          r"that|this|again|one more time|a bit|a little|even|much|way|go|into|in|with|the|i want|for me|than that)\b)*$", re.I)
_TOO_HARD = re.compile(r"^\W*(?:that(?:'s| is| was) |it(?:'s| is| was) |still )?(?:way |far |much )?too (?:hard|complicated|complex|technical|advanced|difficult|much)(?: for me)?\W*$", re.I)
_TOO_EASY = re.compile(r"^\W*(?:that(?:'s| is| was) |it(?:'s| is| was) )?(?:way |far |much )?too (?:simple|easy|basic|childish|babyish)(?: for me)?\W*$", re.I)
# The whiteboard. With the word itself: "show me DNS on the whiteboard", "put subnetting on the board".
_BOARD = re.compile(_LEAD + r"(?:(?:show|draw|sketch|put|illustrate|diagram|explain|teach)\b(?: me| us)?\s+(?P<a>.{1,80}?)\s+(?:up )?(?:on|at|using|with) "
                    r"(?:the |a |your |my )?(?:white ?board|board)\b|white ?board\s+(?P<b>.{1,80}))", re.I)
_DRAW = re.compile(_LEAD + r"(?:draw|sketch|diagram|illustrate)\s+(?:me\s+|us\s+)?(?P<t>.{1,80})", re.I)
# Words that make "explain X" a request to the tutor on their own: a rung or an age he names.
_RUNG = re.compile(r"\b(?:like|as if|as though)\s+i(?:'?m| am| was| were)\b|\beli5\b|"
                   r"\b(?:to|for) a (?:[\w-]+[- ]year[- ]old|child|kid|baby|toddler|pre[- ]?schooler|kindergartner)\b|"
                   r"\bfor (?:a )?(?:beginners?|dummies)\b|\b(?:level|version)\b|\bexam[- ]ready\b|\bfor (?:the|my) (?:exam|test)\b", re.I)
# "from my IT Fundamentals course", "in my networking class", "out of my notes": a course he names, whatever its title.
_COURSE_REF = re.compile(r"\b(?:from|in|out of|of|on|using|for)\s+(?:my|the|our)\s+(?:[\w&'-]+\s+){0,4}?"
                         r"(?:course|class|notes|textbook|book|slides|materials?|chapter|module|unit|lecture|syllabus)\b", re.I)
# What may be taught from general knowledge when his library has nothing on it: a plain subject of a few words, not a
# pronoun, a thing on his screen or a question of fact about his day ("explain what happened", "explain this error").
_PLAIN_BAD = set("my me i we you your yours our us this that these those it its them their they he she him her yourself myself "
                 "everything anything something nothing happened yesterday today tomorrow tonight error errors bug bugs file files folder code screen window page email "
                 "message result results log logs output traceback why whether if when who where which "
                 # the conversation itself, or what Jarvis is doing: not a subject
                 "above below previous earlier last next before after prior former latter one ones other another same rest "
                 "problem issue situation plan plans lag step steps going doing "
                 # everyday things that have their own handlers or are plain chat
                 "joke jokes news score scores rules bill invoice recipe workout workouts training exercise exercises gym "
                 "answer answers reply response question questions "
                 # a language lesson is not a general-knowledge subject
                 "word words phrase phrases grammar vocabulary pronunciation lesson french spanish creole kreyol haitian".split())
_CONTRACTION = re.compile(r"(?:'(?:re|m|ve|ll|d)|n't|^(?:what|that|it|there|here|let|who|how|where|he|she)'s)$")
_OWN = re.compile(r"^(?:this|that|these|those|my|your|our|his|her|their)\b", re.I)
_ARTICLE = re.compile(r"^(?:a|an|the|my|your|our|some|me|up|out|in|on)\b\s*", re.I)
# Whole lines only, and only in the very next turn: "I don't get it." (not "I don't understand why my code fails").
_LOST = re.compile(r"^\W*(?:sorry,? )?(?:i (?:do not|don'?t|dont) (?:understand|get it|get that|follow)|that (?:didn'?t|did not|doesn'?t) (?:help|make sense)|"
                   r"i'?m (?:still )?(?:confused|lost)|still (?:confused|lost))(?: (?:it|that|this|the (?:question|answer)))?\W*$", re.I)
_CARDS_MAKE = re.compile(_LEAD + r"(?:make|create|generate|build|give me|write)\s+(?:me\s+)?(?:some\s+|a few\s+|\d{1,2}\s+|new\s+)*flash ?cards?\b", re.I)
_CARDS_REVIEW = re.compile(_LEAD + r"(?:(?:review|go (?:through|over)|do|practice|start|study|quiz me on)\s+(?:my\s+|the\s+|today'?s\s+|due\s+)*flash ?cards?\b|review my (?:due )?cards\W*$)|"
                           r"^\W*(?:what|which|how many) (?:flash ?)?cards are due\b|^\W*(?:are|do i have) (?:any |there any )?flash ?cards due\b", re.I)
_PROGRESS = re.compile(_LEAD + r"(?:show|tell|give) me my (?:study|learning|course) progress\b|^\W*(?:what'?s|how'?s|how is|what is) my (?:study|learning|course) progress\b|"
                       r"^\W*what should i (?:study|review|revise)(?: next| now| today| first| tonight)?(?: for (?:my |the )?[\w ]{0,40}(?:exam|test|course|class|final|midterm))?\W*$|"
                       r"^\W*how am i doing (?:in|on|with) (?:my |the )?(?:stud(?:y|ies|ying)|courses?|class(?:es)?|exam prep)\W*$|"
                       r"^\W*what are my (?:knowledge gaps|weak (?:spots|areas|points|topics|concepts))\b|^\W*what am i weak (?:on|at|in)\W*$", re.I)
_DOING = re.compile(r"^\W*(?:how am i doing|what should i (?:study|review|revise)|what am i weak) (?:in|on|with|at|for)\b", re.I)
_PLAN = re.compile(_LEAD + r"(?:make|create|build|give|show|write)\s+(?:me\s+)?(?:a|my|the)\s+study (?:plan|schedule)\b|^\W*what'?s my study (?:plan|schedule)\b|"
                   + _LEAD.lstrip("^") + r"teach me everything\b.{0,60}\b(?:exam|test|final|midterm)\b|"
                   + _LEAD.lstrip("^") + r"(?:prepare|get) me (?:ready )?for (?:my |the |this )[\w ]{0,40}(?:exam|final|midterm)\W*$", re.I)
# The only talk that SETS an exam date: "set my exam date ..." or "my exam for <a course of his> is on <date>".
_SET_DATE = re.compile(_LEAD + r"set (?:my|the) (?:[\w ]{0,30} )?exam date\b", re.I)
_EXAM_IS = re.compile(r"^\W*my\b.{0,60}\b(?:exam|final|midterm)\b.{0,40}\bis\b", re.I)
_COURSES = re.compile(r"^\W*(?:what|which) (?:study )?courses (?:do i have|am i (?:taking|studying))\b|" + _LEAD.lstrip("^") + r"(?:list|show)(?: me)? my (?:study )?(?:courses|study materials?)\b", re.I)
_SEARCH = re.compile(r"^\W*(?:what|where) (?:do|does) my (?:notes|textbook|book|slides|study material|course material)\s+(?:say|cover|mention|talk)(?: about)?\s+(?P<q>.+)", re.I)
_STOP = re.compile(r"^\W*(?:(?:stop|end|quit|exit|cancel|finish)(?: (?:the|this|my))? (?:quiz|review|session|studying|study session|test|cards?|flash ?cards?)|"
                   r"i'?m done (?:with (?:the |this )?(?:quiz|review|cards|flash ?cards)|studying)|(?:that'?s enough|no more) (?:questions|cards|studying|quiz))(?: for now| please)?\W*$", re.I)
_SKIP = re.compile(r"^\W*(?:skip|pass|never ?mind|skip (?:it|this|that)(?: one| question)?|next question|next card)\W*$", re.I)
_GIVE_UP = re.compile(r"^\W*(?:i (?:do not|don'?t|dont) know|no idea|not sure|idk|i give up|(?:just )?tell me(?: the answer)?|show me the answer|what'?s the answer)\W*$", re.I)
_HINT = re.compile(r"^\W*(?:a |give me a |can i (?:have|get) a )?hint(?: please)?\W*$", re.I)
# A line that opens like an order, a question to Jarvis, a greeting or thanks is never a written answer.
_NOT_ANSWER = re.compile(
    r"^\W*(?:(?:hey|ok|okay)[,.!]?\s+)?(?:jarvis\b|please\b|stop\b|play\b|pause\b|resume\b|mute\b|unmute\b|open\b|close\b|launch\b|start\b|turn\b|set\b|add\b|"
    r"remove\b|delete\b|send\b|email\b|text\b|call\b|message\b|tell\b|show\b|give\b|find\b|search\b|google\b|look\b|lock\b|unlock\b|shut\b|"
    r"restart\b|remind\b|schedule\b|book\b|buy\b|order\b|skip\b|next\b|previous\b|volume\b|louder\b|quieter\b|read\b|write\b|save\b|"
    r"create\b|make\b|run\b|check\b|what\b|what'?s\b|who\b|who'?s\b|when\b|where\b|why\b|how\b|can\b|could\b|would\b|will\b|do\b|does\b|did\b|"
    r"is\b|are\b|am\b|should\b|hey\b|hi\b|hello\b|thanks\b|thank\b|good (?:morning|night|evening|afternoon)\b|bye\b|goodbye\b|ok\b|okay\b|"
    r"never\b|cool\b|nice\b|great\b|lol\b|haha\b|yes\b|no\b|yeah\b|nope\b|sure\b|wait\b|hold\b|sorry\b|let'?s\b|i (?:want|need|would like|'d like)\b)", re.I)
_GUESS = re.compile(r"^\W*(?:is (?:it|that)|would (?:it|that) be|could (?:it|that) be|is the answer)\b", re.I)      # "is it DHCP?" is an answer
_STATEMENT = re.compile(r"^\W*(?:i (?:think|believe|guess|would|will|should)|i'd |maybe|probably|it'?s|it is|it |its |they |that'?s|that is|the |an? |because |when |by |to |you |"
                        r"something|some kind)", re.I)
_STOPWORDS = set("a an and are as at be by for from has have how in is it its of on or that the this to was what when where which who why will with "
                 "does do can you your i me my about into than then them they their there term fits description words own explain".split())
_FILLER = re.compile(r"\b(?:please|jarvis|can you|could you|would you|to me|for me|me|again|just|hey|ok|okay|now|then|and|the concept of|about)\b", re.I)
_THIS = {"", "this", "that", "it", "this one", "that one", "this question", "that question", "the question", "the answer", "that answer", "this card", "why"}
_MONTHS = {m: n for n, m in enumerate("january february march april may june july august september october november december".split(), 1)}


def maybe(text: str) -> bool:
    """Could this line be a study request at all? (One regex; the caller skips everything else when it is not.)"""
    return bool(_MAYBE.search(text or ""))


def exam_date(text: str, today: date | None = None) -> str:
    """"2026-12-05", "December 5", "the 5th of December", "in 2 weeks", "tomorrow", "next friday" -> ISO date or ""."""
    today = today or date.today()
    low = text.lower()
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", low)
    if m:
        return m.group(0)
    names = "|".join(_MONTHS)
    m = re.search(rf"\b({names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", low) or re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({names})\b", low)
    if m:
        a, b = m.groups()
        month, day = (_MONTHS[a], int(b)) if a in _MONTHS else (_MONTHS[b], int(a))
        try:
            when = date(today.year, month, day)
            return (when if when >= today else date(today.year + 1, month, day)).isoformat()
        except ValueError:
            return ""
    m = re.search(r"\bin\s+(\d{1,3}|a|one|two|three|four|five|six)\s+(day|week|month)s?\b", low)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}.get(m.group(1)) or int(m.group(1))
        return (today + timedelta(days=n * {"day": 1, "week": 7, "month": 30}[m.group(2)])).isoformat()
    if re.search(r"\btomorrow\b", low):
        return (today + timedelta(days=1)).isoformat()
    days = "monday tuesday wednesday thursday friday saturday sunday".split()
    m = re.search(rf"\b(?:next|this|on)\s+({'|'.join(days)})\b", low)
    if m:
        ahead = (days.index(m.group(1)) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).isoformat()
    return ""


def _course_in(text: str, titles) -> str:
    """The course he named: a known title whose distinctive words are all in his sentence, the longest first."""
    low = re.sub(r"[^a-z0-9]+", " ", text.lower())
    best = ""
    for title in titles:
        words = [w for w in re.sub(r"[^a-z0-9]+", " ", title.lower()).split() if w not in ("course", "class", "the", "to", "of", "and", "intro")]
        if words and all(re.search(rf"\b{re.escape(w)}\b", low) for w in words) and len(title) > len(best):
            best = title
    return best


def level_of(text: str) -> str:
    """What the sentence asks of the ladder: a rung he named, "simpler" ("like I'm five", "in simple terms"), "deeper",
    or "" for the rung he is on."""
    return ladder.level_in(text)


def topic_of(text: str) -> str:
    """What is left of "can you explain subnetting to me like I'm five, please" -> "subnetting"."""
    return _topic(_EXPLAIN.sub(" ", text, count=1))


def plain_topic(topic: str) -> bool:
    """A subject he can be taught from general knowledge: one to five plain words ("photosynthesis", "how volcanoes work"
    -> "volcanoes"), with no pronoun, path, number or thing on his screen in it."""
    words = re.findall(r"[a-z']+|\S", (topic or "").lower())
    if not 1 <= len(words) <= 5 or re.search(r"[\d\/_@:=<>{}()\[\]]|\.\w{2,4}\b", topic or ""):
        return False
    if words[0] == "to" or any(_CONTRACTION.search(w) for w in words):      # "to cook pasta" is a task; "what you're doing" is talk
        return False
    return not any(w in _PLAIN_BAD for w in words) and bool(re.search(r"[a-z]{3}", topic.lower()))


def _topic(t: str) -> str:
    t = _FILLER.sub(" ", _COURSE_REF.sub(" ", _FROM_MINE.sub(" ", ladder.strip(t))))
    t = " ".join(re.sub(r"[^\w\s./+#'-]", " ", t).split()).strip(" .'-")
    t = re.sub(r"^(?:how|what)\s+(?:is\s+|are\s+|does\s+|do\s+)?|\s+(?:works?|is|are|looks? like)$", "", t, flags=re.I)
    return t.strip(" .'-")


def _board_request(said, course, context, known):
    """"show me DNS on the whiteboard" / "draw subnetting" -> the whiteboard skill, when the topic is his study
    material. A bare "draw a ..." / "draw the ..." is only a study request in the turn or two after a study reply."""
    m, bare = _BOARD.search(said), False
    if m:
        raw = m.group("a") or m.group("b")
    else:
        m, bare = _DRAW.search(said), True
        if not m:
            return None
        raw = m.group("t")
        if not context and _ARTICLE.match(raw.strip()) and not raw.strip().lower().startswith("the "):
            return None                                          # "draw a bath", "draw me a picture": not his studying
    named = _ARTICLE.sub("", _topic(raw))
    # Outside studying, "the <one everyday word>" is a thing, not a topic ("draw the line", "put the key on the board"),
    # even when the word is somewhere in his course. Saying "whiteboard" makes it a study request again.
    weak = bare or not re.search(r"white ?board", m.group(0), re.I)
    if not context and weak and _ARTICLE.match(raw.strip()) and len(named.split()) < 2:
        return None
    topic = named
    if topic.lower() in _THIS:
        if context:
            return "study_whiteboard", {"topic": "", "level": level_of(said), "course": ""}
        return None
    if (bare and len(topic.split()) > 4) or not known(topic):
        return None
    return "study_whiteboard", {"topic": topic, "level": level_of(said), "course": course}


def _words(text) -> set:
    return {w for w in re.findall(r"[a-z0-9]{2,}", str(text or "").lower()) if w not in _STOPWORDS}


def looks_like_answer(said: str, question: dict, picks=None, orders=None, fresh: bool = True) -> bool:
    """Is this line an answer to the question on the table? question: {"options": [...]} for multiple choice, else the
    written question's {"stem", "answer", "quote"} (or a card's front/back under the same keys). picks(said) -> does it
    name an option (the engine's own reading). orders(said) -> would the normal path treat it as a command or small
    talk. fresh: the question was asked moments ago (an unrelated-sounding statement is only taken then)."""
    if question.get("options"):
        return bool(picks and picks(said))
    if "?" in said and not _GUESS.match(said):
        return False
    if _NOT_ANSWER.match(said) and not _GUESS.match(said):
        return False
    about = _words(question.get("answer")) | _words(question.get("quote")) | _words(question.get("stem"))
    relevant = bool(_words(said) & about)
    # One word from the question's own subject ("DHCP") is the answer, even though a lone noun reads like a clipped
    # order to the rest of Jarvis. Anything longer that the normal path would act on is left to it.
    if orders and orders(said) and not (relevant and len(said.split()) == 1):
        return False
    if relevant:
        return True                                              # it talks about what was asked
    return fresh and len(said.split()) >= 3 and bool(_STATEMENT.match(said))


def call_for(text: str, question: dict | None = None, context: int = 0, titles=(), known=lambda topic: False,
             picks=None, orders=None, fresh: bool = True, general: bool = True):
    """general: may a plain subject his material has nothing on be taught from general knowledge? (False while a language
    lesson is open: then only what is in his material is the tutor's.)
    question: the voice/chat quiz question or card on the table, or None. context: turns since the last study reply
    (1 = this is the very next turn, 2 = the one after, 0 = none). titles: his course titles (a list, or a callable
    that returns it: only called once the line has study wording). known(topic): is it in his study material?"""
    said = " ".join((text or "").split())
    if not said or len(said) > 600:
        return None
    if question is not None:
        if _STOP.match(said):
            return "study_stop", {}
        if _SKIP.match(said) or _GIVE_UP.match(said) or _HINT.match(said):
            return "study_answer", {"answer": said}
    if context or maybe(said):                                   # right after a study reply "I don't get it" has no study word
        found = _request(said, titles() if callable(titles) else titles, context, known, general)
        if found:
            return found
    if question is not None and looks_like_answer(said, question, picks, orders, fresh):
        return "study_answer", {"answer": said}
    return None


def _request(said, titles, context, known, general=True):
    course = _course_in(said, titles)
    quiz = _QUIZ.search(said)
    if quiz and (re.search(r"\bme$", quiz.group(0), re.I) or course or _QUIZ_TAIL.match(said[quiz.end():])):
        m, n = _CHAPTER.search(said), _COUNT.search(said)
        exam = bool(_EXAM.search(said))
        chapter = m.group(0) if m else ""
        if not chapter:                                          # "quiz me on switching": a chapter or topic by name
            rest = re.split(r"\b(?:on|about|over)\b", said, 1, flags=re.I)
            topic = rest[1] if len(rest) > 1 else ""
            for word in re.findall(r"[A-Za-z0-9]+", course):
                topic = re.sub(rf"\b{re.escape(word)}\b", " ", topic, flags=re.I)
            topic = re.sub(r"\b(?:everything|all|in|of|from|my|course|class|material|notes|with|\d+|five|ten|fifteen|twenty|questions?)\b", " ", topic, flags=re.I)
            chapter = " ".join(_FILLER.sub(" ", topic).replace("?", " ").replace(".", " ").replace("!", " ").split())
        count = (_NUMBERS.get(n.group(1).lower()) or int(n.group(1))) if n else (15 if exam else 5)
        return "study_quiz", {"course": course, "chapter": chapter, "count": count, "kind": "exam" if exam else "mixed"}
    if _CARDS_MAKE.search(said):
        m = _CHAPTER.search(said)
        return "study_make_flashcards", {"course": course, "chapter": m.group(0) if m else "", "count": 8}
    if _CARDS_REVIEW.search(said):
        return "study_review_cards", {"course": course}
    if _SET_DATE.search(said) or (course and _EXAM_IS.search(said)):
        when = exam_date(said)
        if when:
            return "study_plan", {"course": course, "exam_date": when}
    if _PLAN.search(said):
        return "study_plan", {"course": course, "exam_date": ""}       # a plan from the saved date; talk never sets one here
    if _PROGRESS.search(said) or (course and _DOING.search(said)):
        return "study_progress", {"course": course}
    if _COURSES.search(said):
        return "study_courses", {}
    m = _SEARCH.search(said)
    if m and m.group("q").strip(" ?.!"):
        return "study_search", {"query": m.group("q").strip(" ?.!"), "course": course}
    board = _board_request(said, course, context, known)
    if board:
        return board
    level = level_of(said)
    if context and level and _ONLY_FILLER.match(ladder.strip(said)):
        return "study_explain_again", {"level": level}           # "simpler", "more detail", "now like I'm one"
    if context and (_TOO_HARD.match(said) or _TOO_EASY.match(said)):
        return "study_explain_again", {"level": "simpler" if _TOO_HARD.match(said) else "deeper"}
    if context and _AGAIN.search(said):
        return "study_explain_again", {"level": level}           # another way, at the rung he is on
    if context and _STILL.search(said):
        return "study_explain_again", {"level": level or "simpler"}
    if context == 1 and _LOST.match(said):
        return "study_explain_again", {"level": level or "simpler"}
    if _EXPLAIN.search(said):
        topic = topic_of(said)
        if topic.lower() in _THIS:
            if context == 1 or (context and (_REFERS.search(said) or level in ladder.RUNGS)):
                return "study_explain", {"topic": "", "level": level, "course": ""}
        elif not context and _OWN.match(topic):
            return None                                          # "explain this file", "explain my password": not a course topic
        else:
            topic = re.sub(r"^(?:a|an|the)\s+", "", topic, flags=re.I)
            # "explain X" is an instruction to the tutor: from his material when X is in it, else from general knowledge
            # (the tutor says so once). A subject that is not a plain one ("explain this error", "explain what happened") stays talk.
            # "walk me through X" is the tutor only for what is in his material (it is as often a workout or a task), and
            # there is no general-knowledge lecture while a language lesson is open.
            walk = re.search(r"\bwalk me through\b", said, re.I)
            if known(topic) or (general and not walk and plain_topic(topic)):
                return "study_explain", {"topic": topic, "level": level, "course": course}
    return None
