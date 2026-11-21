"""Getting trustworthy information out of search results, in code, for a small local model (2026-11-15).

Everything here is deterministic and offline (the search itself lives in core/websearch.py):

  rewrite(question)      the question as a search query: filler taken off, "last night" / "tomorrow" turned into the
                         real date, the year added when the question is about NOW (never for history or how-to questions)
  split_questions(q)     "the weather in Springfield and who won the Lakers game" -> two searches
  rank(rows, question)   primary and recent sources first, forums and social sites last
  assess(pack, question) the cross-check: how many independent sources (copies of one wire story are one), do the figures
                         that ANSWER the question (score, version, date, price...) agree or conflict, how old is the newest
                         dated result
  evidence_note(...)     that verdict as lines for the model, so it cannot state one site's number as settled fact
  answer(pack, ...)      2-4 spoken sentences from the results alone: figures and sentences are quoted as found,
                         never composed, with the source name and date, and the honest line when nothing is reliable
  followup(text, pack)   "tell me more about the second one", "what about tomorrow?", "where did you get that?"

Nothing here invents a figure: a number or date in an answer is one that a result printed. Nothing here may throw on a
strange row or date: a bad row degrades to "no date" / "not usable", and a failed cross-check says it could not check."""
import math
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MONTH_RX = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def today() -> date:
    return datetime.now().date()


def _long(d: date) -> str:
    return f"{_MONTHS[d.month - 1].capitalize()} {d.day}, {d.year}"


def _s(x) -> str:
    return "" if x is None else str(x)


# -- dates a provider printed ---------------------------------------------------------------------------------------------------
def _month_no(name: str) -> int:
    return _MONTHS.index(next(m for m in _MONTHS if m.startswith(name[:3].lower()))) + 1


def _mk(y, m, d) -> str | None:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def parse_date(value, now: date | None = None) -> str | None:
    """A date a provider printed, in whatever form ('Nov 12, 2026', '12 Nov 2026', '2 hours ago', 'Yesterday', 2026-11-12T10:00Z,
    20261112, '25/12/2026'), as YYYY-MM-DD; None when it is not a date or is ambiguous ('12/11/2026': day first or month
    first?). Never raises."""
    try:
        now = now or today()
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        s = _s(value).strip()
        if not s or s.upper() in ("N/A", "NONE", "NULL", "UNKNOWN"):
            return None
        low = s.lower()
        if low in ("just now", "now") or re.match(r"today\b", low):
            return now.isoformat()
        if re.match(r"yesterday\b", low):
            return (now - timedelta(days=1)).isoformat()
        m = re.search(r"(?<![\w])(\d+|an?|one)\s?(second|sec|minute|min|hour|hr|day|week|wk|month|mo|year|yr|h|d|w|y)s?\.?\s+ago\b", low)
        if m:
            n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
            unit = m.group(2)
            days = (0 if unit in ("second", "sec", "minute", "min", "hour", "hr", "h") else 1 if unit in ("day", "d") else
                    7 if unit in ("week", "wk", "w") else 30 if unit in ("month", "mo") else 365) * n
            return (now - timedelta(days=days)).isoformat()
        m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", s)
        if m:
            return _mk(*m.groups())
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s) or re.fullmatch(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})", s)
        if m:
            return _mk(*m.groups())
        m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RX})\.?(?:,?\s+(\d{{4}}))?(?!\d)", s, re.I)
        m2 = re.search(rf"\b({_MONTH_RX})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?(?!\d)", s, re.I)
        if m or m2:
            day, mon, year = (m.group(1), m.group(2), m.group(3)) if m else (m2.group(2), m2.group(1), m2.group(3))
            iso = _mk(year or now.year, _month_no(mon), day)
            if iso and not year and date.fromisoformat(iso) > now + timedelta(days=2):
                iso = _mk(now.year - 1, _month_no(mon), day)
            return iso
        m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
            if a > 12 >= b:
                return _mk(y, b, a)
            if b > 12 >= a:
                return _mk(y, a, b)
            return None                                       # 12/11/2026: day first or month first? not guessed
    except Exception:                                         # noqa: BLE001 - a date is never worth an exception
        return None
    return None


def say_date(iso, now: date | None = None) -> str:
    """'2026-11-13' as it is spoken: 'yesterday', 'today', 'November 13' (with the year when it is another year)."""
    now = now or today()
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    if d == now:
        return "today"
    if d == now - timedelta(days=1):
        return "yesterday"
    base = f"{_MONTHS[d.month - 1].capitalize()} {d.day}"
    return base if d.year == now.year else f"{base}, {d.year}"


# -- sentences that do not break at an abbreviation ---------------------------------------------------------------------------
_ABBR = {"inc", "ltd", "llc", "corp", "co", "st", "mt", "ft", "dr", "mr", "mrs", "ms", "jr", "sr", "prof", "vs", "etc", "gen", "sen", "rep",
         "gov", "lt", "col", "sgt", "capt", "ave", "blvd", "dept", "approx", "fig", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
         "sept", "oct", "nov", "dec", "cf", "eg", "ie"}


def _ends_abbreviation(sentence: str) -> bool:
    words = sentence.split()
    last = words[-1] if words else ""
    return last.endswith(".") and bool(re.fullmatch(r"(?:[A-Za-z]\.)+", last) or last[:-1].lower() in _ABBR)


def split_sentences(text) -> list[str]:
    """Sentences, never broken after Inc. / St. / vs. / Dr. / U.S. / an initial."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", _s(text).strip()):
        if out and _ends_abbreviation(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return [p for p in out if p.strip()]


# -- the question as a search query --------------------------------------------------------------------------------------
_FILLER_LEAD = re.compile(r"^\W*(?:(?:um+|uh+|er+|erm|so|well|okay|ok|hey|yo|and|but|then|basically|actually|honestly|also|you know|"
                          r"i mean|jarvis|sir|please)\b\W*)+", re.I)
_FILLER_MID = re.compile(r"\b(?:um+|uh+|erm|you know|basically|honestly|literally)\b,?\s*", re.I)
_FILLER_TAIL = re.compile(r"[\s,]*(?:please|thanks|thank you|for me|right now|at the moment|these days|you know|or something|"
                          r"or whatever|and stuff|and all that|sir|jarvis)\W*$", re.I)
_REL = (
    (re.compile(r"\bthe day before yesterday\b", re.I), -2),
    (re.compile(r"\bthe day after tomorrow\b", re.I), 2),
    (re.compile(r"\btomorrow(?:'s)?(?: (?:morning|afternoon|evening|night))?\b", re.I), 1),
    (re.compile(r"\b(?:last night(?:'s)?|yesterday(?:'s)?(?: (?:morning|afternoon|evening|night))?)\b", re.I), -1),
    (re.compile(r"\b(?:today(?:'s)?|tonight(?:'s)?|this (?:morning|afternoon|evening))\b", re.I), 0),
)
_HAS_DATE = re.compile(rf"\b(?:19|20)\d\d\b|\b{_MONTH_RX}\.? \d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}(?:st|nd|rd|th)? (?:of )?{_MONTH_RX}\b|"
                       r"\bthe \d{1,2}(?:st|nd|rd|th)\b", re.I)
_PERIOD = re.compile(r"\b(this|next|last) (week|weekend|month)\b", re.I)
# What is true only right now. NOT definitions ("what is a stock split"), general knowledge ("the boiling temperature of water", "is Python
# open source"), how-to, or a season ("the weather in March"): those are timeless and old sources are fine.
_MONTHS_FULL = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
_STATIC = re.compile(
    r"^\W*(?:what(?:'s| is| are| was)|whats|define|explain)\s+(?:a|an)\b|"
    r"\b(?:means?|meaning|definition|average|typical|usually|generally|in general|boiling|freezing|melting|credit|highest|lowest|laws?|rules)\b|"
    rf"\b(?:in|during|every|each)(?: the)? (?:{_MONTHS_FULL}|winter|spring|summer|fall|autumn)\b|\b(?:this|next|last) (?:winter|spring|summer|fall|autumn)\b|"
    r"\bcurrent time\b|\btime (?:is it )?in\b|\bwhat time is it\b|\bcurrent (?:through|in amps|flow)\b|"
    r"\bopen[- ](?:source|world|ai|book|ended|minded|access|office)\b|\bthe rain in\b|\bwhat causes\b|"
    r"\bis (?:the )?(?:door|window|file|tab|folder|port|valve|drawer) open\b", re.I)
_STATIC_CASE = re.compile(r"\b(?:SAT|ACT|IQ|GPA)\b")
_VOLATILE = re.compile(
    r"\b(?:weather|forecast|standings|exchange rate|stock (?:price|market)|share price|ticker)\b|"
    r"\btemperature (?:in|outside|at|here|near)\b|"
    r"\b(?:how(?:'s| is| are) (?:the )?traffic|traffic (?:report|update|conditions|on|near|around|right now|now|jam)|is there (?:any )?traffic)\b|"
    r"\b(?:is it|will it|going to|gonna|chance of)\s+(?:\w+\s+){0,3}(?:rain|snow|storm|sleet|hail)\w*|"
    r"\b(?:bitcoin|btc|ethereum|crypto\w*)\b(?=.*\b(?:price|worth|cost|much|rate|trading|value)\b)|"
    r"\b(?:price|worth|cost|much|rate|trading|value)\b.*\b(?:bitcoin|btc|ethereum|crypto\w*)\b|"
    r"\bhow (?:is|are|'s) .{0,30}\b(?:stock|stocks|shares) doing\b|"
    r"\bis (?:the |it |a )?(?:[\w'.-]+ ){0,3}open\b(?! (?:source|world|ai|book|ended|minded|access|office|edge|chrome))", re.I)


_COMMODITY = r"(?:gold|silver|oil|gas(?:oline)?|euros?|dollars?|yen|pounds?|mortgage|interest)"
_VOLATILE_QUOTE = re.compile(
    rf"\b{_COMMODITY}\b.*\b(?:price|prices|cost|worth|rates?|gallon|per ounce)\b|\b(?:price|prices|cost|worth|rates?)\b.*\b{_COMMODITY}\b|"
    rf"\bhow much is (?:a |the |an )?{_COMMODITY}\b|\bgallon of gas\b|\bmortgage rates?\b|\b(?:in stock|sold out|restock\w*)\b", re.I)


def _volatile(q: str) -> bool:
    """Does the question ask for what is true RIGHT NOW (weather, a game's score, a quote, traffic, whether a place is open)?"""
    if _STATIC.search(q) or _STATIC_CASE.search(q):
        return False
    if _VOLATILE.search(q) or _VOLATILE_QUOTE.search(q):
        return True
    return bool(re.search(r"\bscores?\b", q, re.I)
                and (re.search(r"\b(?:game|match|vs\.?|versus|playoffs?|final|tonight|today|live)\b", q, re.I) or re.search(r"\bof (?:the )?[A-Z]", q))
                and not re.search(r"\b(?:credit|good|average|highest|lowest|passing|perfect|high)\b", q, re.I))


_NOW_MARK = re.compile(r"\b(?:today|tonight|tomorrow|yesterday|last night|right now|currently|this (?:morning|afternoon|evening|week|weekend))\b", re.I)
# Present-day facts: the year makes the search engine rank this year's page first.
_CURRENT = re.compile(r"\b(?:latest|newest|current(?:ly)?|recent(?:ly)?|upcoming|news|headlines|release date|this year|"
                      r"who (?:is|are) the (?:\w+ ){0,2}(?:president|prime minister|ceo|leader|champions?|coach|governor|mayor)|"
                      r"who won (?:the )?(?:[\w'-]+ ){0,3}(?:game|match|race|final|series|election|cup|bowl|championship|title|tournament|open|prix|primary))\b", re.I)
_TIMELESS = re.compile(r"\b(?:first|original|originally|ever|history|historical|invented|inventor|founded|of all time|ancient|meaning of|"
                       r"definition|what causes|why (?:do|does|is|are)|how (?:does|do) .{0,40} work|civil war|world war|cold war|revolution|"
                       r"battle of|treaty|empire|dynasty|century|medieval|renaissance|crusades?)\b", re.I)
_HOWTO = re.compile(r"^\W*(?:how (?:do|does|did|to|can|should|would|could)\b|how (?:many|much)\b.{0,40}\b(?:do|does|should|need|per)\b|"
                    r"can i\b|should i\b|why\b)", re.I)


_PAST_TIME = re.compile(
    r"\blast (?:year|season|decade|century|january|february|march|april|may|june|july|august|september|october|november|december)\b|\blast year's\b|"
    r"\b(?:\w+|\d+) (?:years?|decades?|seasons?|centuries) ago\b|\b(?:a|one) (?:year|decade|season) ago\b|\bprevious(?:ly)?\b|"
    r"\bin the (?:(?:19|20)?\d0s|nineties|eighties|seventies|sixties|fifties)\b|\bback in\b(?! stock)|\bthe year before\b|\bformer(?:ly)?\b", re.I)


_NOW_WORD = re.compile(r"\b(?:today|now|current(?:ly)?|tonight|right now|latest|newest|this (?:week|weekend|morning|afternoon|evening|month)|at the moment)\b", re.I)


def _historic(q: str, now: date) -> bool:
    """A question about a year before this one, or about history or the past ("last year", "two years ago", "in the 90s"): nothing
    about it ages and no year is added. Not when it ALSO asks about now ("the price of bitcoin today compared to last year")."""
    if _TIMELESS.search(q):
        return True
    if _NOW_WORD.search(q):
        return False
    return any(int(y) < now.year for y in re.findall(r"\b((?:19|20)\d\d)\b", q)) or bool(_PAST_TIME.search(q))


def _recent_date(q: str, now: date) -> bool:
    """A full date in the question within two weeks of today (the one rewrite() put there for "last night")."""
    for m in re.finditer(rf"\b({_MONTH_RX})\.? (\d{{1,2}}),? (\d{{4}})\b", q, re.I):
        iso = _mk(m.group(3), _month_no(m.group(1)), m.group(2))
        if iso and abs((date.fromisoformat(iso) - now).days) <= 14:
            return True
    return False


_DAY_RX = re.compile(r"\b(?:(this|next|last|on) )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(?: (?-i:morning|afternoon|evening|night)\b)?", re.I)
_DAY_PREFIX = {"black", "good", "cyber", "super", "ash", "holy", "casual", "boxing", "maundy", "easter", "taco"}
_PAST = re.compile(r"\b(?:won|did|was|were|beat|lost|played|happened|had|scored|ended|finished|said|died|rained|snowed|raining)\b", re.I)


def _is_title(m, q: str) -> bool:
    """Is the matched day-word part of a title or name ("Today's Show", "Today Show hosts", "Tomorrow Never Dies", "The Day After
    Tomorrow", "Last Night in Soho")? Capitalised in the middle of a sentence, or at the start with a capitalised word after it."""
    text = m.group(0)
    words = text.split()
    if sum(1 for w in words if w[:1].isupper()) >= 2:                # "the Day After Tomorrow", "Last Night": a title whatever the first word
        return True
    if not text[:1].isupper():
        return False
    after = q[m.end():].split()
    return m.start() > 0 or bool(after and after[0][:1].isupper()) or (len(words) >= 2 and all(w[:1].isupper() for w in words))


def _resolve_weekdays(q: str, now: date) -> tuple[str, bool]:
    """'on Friday' / 'this Wednesday' / 'last Monday' as the date: the coming one, or the past one for a question in the past
    tense ('who won ... on Friday'). 'Black Friday' is a name and stays."""
    hit = []

    def repl(m):
        prefix = (m.group(1) or "").lower()
        before = q[:m.start()].split()
        after = q[m.end():].split()
        if before and before[-1].lower() in _DAY_PREFIX and before[-1][:1].isupper():       # Black Friday, Good Friday
            return m.group(0)
        if m.group(2)[:1].isupper() and not prefix and after and after[0][:1].isupper():     # Saturday Night Live, Monday Night Raw
            return m.group(0)
        if re.match(r"\s*the \d{1,2}(?:st|nd|rd|th)\b", q[m.end():], re.I):                  # Friday the 13th is a date already
            return m.group(0)
        wd = _WEEKDAYS.index(m.group(2).lower())
        past = prefix == "last" or (prefix not in ("this", "next") and bool(_PAST.search(q)))
        delta = -((now.weekday() - wd) % 7 or 7) if past else ((wd - now.weekday()) % 7 or (0 if prefix == "this" else 7))
        hit.append(1)
        return ("on " if prefix == "on" else "") + _long(now + timedelta(days=delta))
    return _DAY_RX.sub(repl, q), bool(hit)


_EVENTS = (("super bowl", 2), ("world series", 10), ("stanley cup", 6), ("nba finals", 6), ("masters", 4), ("wimbledon", 7), ("french open", 6),
           ("australian open", 1), ("tour de france", 7), ("kentucky derby", 5), ("indy 500", 5), ("daytona 500", 2), ("march madness", 4),
           ("oscars", 3), ("grammys", 2), ("emmys", 9))


def _event_year(q: str, now: date) -> int | None:
    """"Who won the Super Bowl": the year of the latest edition that has been played (the event's month is over), not this year's
    that has not happened yet."""
    if not re.search(r"\b(?:won|wins|win|winner|champions?)\b", q, re.I):
        return None
    for name, month in _EVENTS:
        if re.search(rf"\b{name}\b", q, re.I):
            return now.year if now.month > month else now.year - 1
    return None


def rewrite(question, now: date | None = None) -> str:
    """The search query for a spoken question: filler off, relative days as real dates, the year for current events (never
    for history or how-to questions). 'um so who won the Lakers game last night' -> 'who won the Lakers game November 14, 2026'."""
    now = now or today()
    q = re.sub(r"\s+", " ", _s(question).replace("’", "'")).strip()
    q = _FILLER_LEAD.sub("", q)
    q = _FILLER_MID.sub("", q)
    for _ in range(2):
        q = _FILLER_TAIL.sub("", q).strip()
    q = q.strip(" \"'?.!,;:")
    if not q:
        return ""
    resolved = False
    for rx, delta in _REL:
        m = rx.search(q)
        if not m or _is_title(m, q):                                      # "Today Show", "Last Night in Soho": names, not days
            continue
        when = _long(now + timedelta(days=delta))
        if m.group(0).lower().endswith("'s"):                             # "today's Lakers game" -> "the Lakers game <date>"
            q = (q[:m.start()] + "the" + q[m.end():]).strip() + " " + when
        else:
            q = q[:m.start()] + when + q[m.end():]
        resolved = True
    if not resolved:
        m = _PERIOD.search(q)
        if m and not _HAS_DATE.search(q):
            shift = {"this": 0, "next": 1, "last": -1}[m.group(1).lower()] if m.group(2).lower() == "month" else 0
            idx = (now.month - 1 + shift) % 12
            year = now.year + ((now.month - 1 + shift) // 12)
            q = f"{q} {_MONTHS[idx].capitalize()} {year}"
            resolved = True
    q, days = _resolve_weekdays(q, now)
    resolved = resolved or days
    if not resolved and not _HAS_DATE.search(q) and not _historic(q, now) and not _HOWTO.match(q):
        event = _event_year(q, now)
        if event:
            q = f"{q} {event}"
        elif _volatile(q):
            q = f"{q} {_long(now)}"
        elif _CURRENT.search(q) and not (_STATIC.search(q) or _STATIC_CASE.search(q)):
            q = f"{q} {now.year}"
    return re.sub(r"\s+", " ", q).strip()[:220]


_WH = r"(?:who|what|when|where|which|how|is|are|did|does|do|will|was|were)"
_STRICT_WH = r"(?:who|what|when|where|which|how|why)"
_AND_Q = re.compile(rf"\s*[,;]\s+(?={_STRICT_WH}\b)|\s*[,;]?\s+(?:and|also|plus)\s+(?:also\s+)?(?={_WH}\b)", re.I)
_QUESTION_START = re.compile(r"^\W*(?:(?:and|also|then|plus)\W+)*(?:who|what|when|where|which|how|why|is|are|do|does|did|will|was|were|can)\b", re.I)
_PRONOUN = re.compile(r"\b(?:he|she|it|they|him|her|his|its|their|them|this|that|those|these)\b", re.I)


_STOPQ = {"who", "what", "when", "where", "which", "how", "why", "is", "are", "was", "were", "did", "does", "do", "will", "the", "a", "an", "of", "in",
          "on", "to", "it", "its", "and", "what's", "how's", "who's", "where's", "when's", "that", "this"}
_FRAGMENT = re.compile(r"^(?:what|how|when|where|who)(?:'s| is| are| was| were| did| does)?\s+(?:the |its |his |her |their )?"
                       r"(?:final |total |exact |current |actual )?(?:score|result|results|time|price|number|answer|temperature|weather|date|cost|name|"
                       r"population|total|winner|reason|record)\b(?!\s+(?:of|in|at|for|on|near)\b)", re.I)


def _self_contained(part: str) -> bool:
    """Can this part be searched on its own? "what was the final score" cannot: it has no subject of its own."""
    if _FRAGMENT.match(part):
        return False
    words = re.findall(r"[A-Za-z0-9']+", part)
    content = [w for w in words if w.lower() not in _STOPQ]
    return len(content) >= 3 or (len(content) >= 2 and any(w[:1].isupper() for w in part.split()[1:]))


def split_questions(question, limit: int | None = None) -> list[str]:
    """A compound spoken question as separate searches ('the weather in Springfield and who won the Lakers game'), in the order
    asked, none dropped (the caller caps and says what it did not search). A part that points back with a pronoun ('and how
    old is he') would lose what it refers to: then the line is NOT split."""
    q = re.sub(r"\s+", " ", _s(question)).strip()
    parts: list[str] = []
    glued: list[str] = []
    for i, sentence in enumerate(split_sentences(q)):
        if i and not _QUESTION_START.match(sentence) and glued:
            glued[-1] += " " + sentence                                     # "apple. pie recipes please" is one topic
        else:
            glued.append(sentence)
    for sentence in glued:
        for x in _AND_Q.split(sentence.strip(" ?.!,;")):
            x = re.sub(r"(?i)^(?:and|also|plus|then|so)\W+", "", x.strip(" ?.!,;"))
            if x:
                parts.append(x)
    if (len(parts) < 2 or any(_PRONOUN.search(p) for p in parts[1:])
            or any(_QUESTION_START.match(p) and len(p.split()) < 3 for p in parts) or not all(_self_contained(p) for p in parts[1:])):               # "Who won, and what was the score?"
        return [re.sub(r"(?i)^(?:and|also|plus|then|so)\W+", "", q.strip(" ?.!,;"))] if q else []
    return parts[:limit] if limit else parts


_DATE_STRIP = (
    re.compile(rf"\s*(?:\b(?:on|for|in)\s+)?\b{_MONTH_RX}\.? \d{{1,2}}(?:st|nd|rd|th)?, (?:19|20)\d\d\b", re.I),
    re.compile(rf"\s*(?:\b(?:on|for|in)\s+)?\b\d{{1,2}} {_MONTH_RX}\.? (?:19|20)\d\d\b", re.I),
    re.compile(rf"\s*(?:\b(?:on|for|in)\s+)?\b{_MONTH_RX}\.? (?:19|20)\d\d\b", re.I),
    re.compile(rf"\s*\b{_MONTH_RX}\.? \d{{1,2}}(?:st|nd|rd|th)?\s*$", re.I),
    re.compile(r"\s*\b(?:19|20)\d\d\s*$"),
)


def base_of(query) -> str:
    """The topic of a rewritten query, with the date or year it added taken off again ('weather in Springfield November 15,
    2026' -> 'weather in Springfield', 'iPhone 16 release date September 2024' -> 'iPhone 16 release date'), so a follow-up can
    put another date on the same topic. A year he said himself in the middle ('who won the 2022 world cup') stays."""
    q = _s(query)
    for rx in _DATE_STRIP:
        q = rx.sub("", q)
    q = re.sub(r"\s+", " ", q).strip(" ,.-")
    return re.sub(r"(?i)\s+(?:on|in|for|at|by|of)$", "", q)


# -- sources: who, how recent, how reliable ----------------------------------------------------------------------------------
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}


def site_of(url) -> str:
    """The registrable site of a URL (espn.com, bbc.co.uk): two results from one site are one source."""
    try:
        host = (urlsplit(_s(url)).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def rows_of(pack) -> list[dict]:
    """The usable result rows of a pack (read pages first): dicts with a URL. Anything else is not a result."""
    if not isinstance(pack, dict):
        return []
    rows = pack.get("sources") or pack.get("results") or []
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("url"), str) and r.get("url") and _parses(r["url"])]


def _parses(url: str) -> bool:
    try:
        urlsplit(url).hostname                                # noqa: B018 - a broken address ("http://[::1/x") raises here
        return True
    except ValueError:
        return False


_URL_DATE = re.compile(r"/((?:19|20)\d\d)/(\d{1,2})(?:/(\d{1,2}))?(?:/|$)")
_LOWQ = ("quora.", "pinterest.", "facebook.", "instagram.", "tiktok.", "youtube.", "youtu.be", "reddit.", "medium.com",
         "answers.com", "ehow.", "twitter.", "x.com", "linkedin.", "yahoo.com/answers", "tumblr.")
_WIRES = ("apnews.com", "reuters.com", "bbc.com", "bbc.co.uk", "npr.org", "pbs.org")
_GENERIC_BRANDS = {"weather", "news", "sports", "today", "times", "time", "health", "money", "food", "games", "music", "maps", "movies", "shop",
                   "store", "blog", "forum", "wiki", "answers", "search", "tech", "apps", "travel", "finance", "stocks", "scores", "live", "daily",
                   "world", "local", "online", "video", "videos", "review", "reviews", "guide", "best", "top", "free", "cheap", "deals", "jobs",
                   "cars", "homes", "home", "school", "people", "data", "info", "free"}


def row_date(row, now: date | None = None) -> tuple[str | None, str]:
    """(YYYY-MM-DD or None, how it is known): the page's own date, the search engine's listing date, or the date in a
    news-style URL path (/2026/11/13/...). A date that cannot be read, or lies in the future, is no date."""
    try:
        now = now or today()
        if not isinstance(row, dict):
            return None, "none"
        for key, how in (("date", "published"), ("listed", "listed")):
            iso = parse_date(row.get(key), now)
            if iso and 1990 <= int(iso[:4]) and date.fromisoformat(iso) <= now + timedelta(days=2):
                return iso, how
        m = _URL_DATE.search(urlsplit(_s(row.get("url"))).path)
        if m:
            iso = _mk(m.group(1), m.group(2), m.group(3) or 1)
            if iso and date.fromisoformat(iso) <= now + timedelta(days=2):
                return iso, "url"
    except Exception:                                         # noqa: BLE001
        pass
    return None, "none"


def window_days(question, now: date | None = None) -> int | None:
    """How old a result may be before it is probably out of date for this question; None when age does not matter (history,
    how-to, anything about a past year)."""
    now = now or today()
    q = _s(question)
    if _historic(q, now):
        return None
    has_now = bool(_NOW_MARK.search(q)) or _recent_date(q, now)
    if (_HOWTO.match(q) or _STATIC.search(q) or _STATIC_CASE.search(q)) and not has_now:
        return None
    if has_now or _volatile(q):
        return 4
    if re.search(r"\b(?:news|headlines|breaking|happening)\b", q, re.I):
        return 10
    if re.search(r"\b(?:latest|newest|current(?:ly)?|most recent|upcoming)\b", q, re.I):
        return 365
    return None


def _age(row, now) -> int | None:
    iso, _ = row_date(row, now)
    return (now - date.fromisoformat(iso)).days if iso else None


_OFFICIAL = {"nvidia": "nvidia.com", "python": "python.org", "blender": "blender.org", "apple": "apple.com", "iphone": "apple.com", "ipad": "apple.com",
             "macos": "apple.com", "microsoft": "microsoft.com", "windows": "microsoft.com", "xbox": "xbox.com", "google": "google.com", "android": "android.com",
             "tesla": "tesla.com", "amazon": "amazon.com", "openai": "openai.com", "github": "github.com", "ubuntu": "ubuntu.com", "mozilla": "mozilla.org",
             "firefox": "mozilla.org", "samsung": "samsung.com", "sony": "sony.com", "playstation": "playstation.com", "nintendo": "nintendo.com",
             "steam": "steampowered.com", "unity": "unity.com", "unreal": "unrealengine.com", "intel": "intel.com", "amd": "amd.com", "rust": "rust-lang.org"}


def is_primary(row, question="") -> bool:
    """A government, university or military page, or the known official site of what the question names (nvidia.com for an NVIDIA
    driver). A site that merely has the question's word in its name (springfield.com for Springfield, mercury.com for Mercury) is not."""
    if not isinstance(row, dict):
        return False
    url = _s(row.get("url")).lower()
    if not url or not _parses(url):
        return False
    host = urlsplit(url).hostname or ""
    if host.endswith((".gov", ".edu", ".mil")) or ".gov." in host:
        return True
    words = set(re.findall(r"[a-z0-9]{3,}", _s(question).lower()))
    return any(_OFFICIAL.get(w) == site_of(url) for w in words)


def rank(rows, question="", now: date | None = None) -> list[dict]:
    """Primary and recent sources first (stable otherwise: the provider's order breaks ties); never drops a row."""
    now = now or today()
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    window = window_days(question, now)

    def score(item):
        i, r = item
        url = _s(r.get("url")).lower()
        site = site_of(url)
        s = -0.15 * i
        if is_primary(r, question):
            s += 1.0                                          # .gov/.edu, or nvidia.com for an NVIDIA driver: the maker's own page
        if site in _WIRES and re.search(r"news|happening|breaking|headlines", _s(question), re.I):
            s += 0.5
        if site == "wikipedia.org" and re.match(r"(?i)\s*(?:who|what) (?:is|was|are)\b", _s(question)):
            s += 0.3
        if any(bad in url for bad in _LOWQ) or "/forum" in url or "/thread" in url:
            s -= 1.0
        age = _age(r, now)
        if window and age is not None:
            s += 1.0 if age <= window else -1.5
        return s
    if len(rows) < 2:
        return rows
    return [r for _, r in sorted(((i, r) for i, r in enumerate(rows)), key=lambda it: -score(it))]


# -- figures: the numbers and dates a result prints ----------------------------------------------------------------------------
_NOT_BEFORE = r"(?<!\d)(?<!\d\.)(?<!\d-)"
_NOT_AFTER = r"(?!\d)(?!\.\d)(?!-\d)"
_SCORE = re.compile(_NOT_BEFORE + r"(\d{1,3})\s?(?:-|–|—|to)\s?(\d{1,3})" + _NOT_AFTER +
                    r"(?!\s?(?:minutes?|hours?|days?|miles?|percent|%|years?|months?|weeks?))")
_SCORE_NAMES = re.compile(r"(?<!\d)(\d{1,3}),\s+(?:the\s+)?[A-Z][A-Za-z.'-]*(?:\s[A-Z][A-Za-z.'-]*)?\s+(\d{1,3})(?!\d)")
_VERSION = re.compile(r"(?<![\w.])v?(\d{1,4}(?:\.\d{1,4}){1,3})(?!\w)(?!\.\w)")
_MAG = r"(?: ?(?:billion|million|thousand|trillion)\b|\s?(?-i:[MBK])(?!\w)|(?-i:[mbk])(?!\w))?"
_MONEY = re.compile(r"\$\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?" + _MAG, re.I)
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?(?:%|percent)", re.I)
_TEMP = re.compile(r"(-?\d{1,3})\s?(?:°\s?([CF])\b|°|degrees(?:\s+(celsius|fahrenheit|c|f)\b)?)", re.I)
_UNITS = {  # unit -> (dimension, factor to the base of that dimension: metres, kilograms, megabytes, metres per second)
    "m": ("length", 1), "metre": ("length", 1), "metres": ("length", 1), "meter": ("length", 1), "meters": ("length", 1),
    "km": ("length", 1000), "kilometre": ("length", 1000), "kilometres": ("length", 1000), "kilometer": ("length", 1000), "kilometers": ("length", 1000),
    "mi": ("length", 1609.344), "mile": ("length", 1609.344), "miles": ("length", 1609.344),
    "ft": ("length", 0.3048), "foot": ("length", 0.3048), "feet": ("length", 0.3048), "cm": ("length", 0.01),
    "inch": ("length", 0.0254), "inches": ("length", 0.0254),
    "kg": ("mass", 1), "kilogram": ("mass", 1), "kilograms": ("mass", 1), "lb": ("mass", 0.45359237), "lbs": ("mass", 0.45359237),
    "pound": ("mass", 0.45359237), "pounds": ("mass", 0.45359237),
    "kb": ("data", 0.001), "mb": ("data", 1), "gb": ("data", 1000), "tb": ("data", 1e6),
    "mph": ("speed", 0.44704), "kph": ("speed", 0.277778), "km/h": ("speed", 0.277778),
    "kib": ("data", 0.001024), "mib": ("data", 1.048576), "gib": ("data", 1073.741824), "tib": ("data", 1099511.627776)}
_UNIT_AFTER = re.compile(r"^\s?(km/h|kph|mph|kilomet(?:re|er)s?|met(?:re|er)s?|miles?|feet|foot|ft|inch(?:es)?|kilograms?|kg|pounds?|lbs?|cm|mi|km|gib|mib|tib|kib|gb|mb|tb|kb|m)\b", re.I)
_COUNT = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?: (?:million|billion|thousand|trillion)|[MBK](?!\w))?")
_DATE_FIG = re.compile(rf"\b({_MONTH_RX})\.? (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (\d{{4}}))?\b|\b(\d{{1,2}}) ({_MONTH_RX})\.?(?: (\d{{4}}))?\b", re.I)

# Figures that are not the answer: a team's record, a quarter score, a price CHANGE, a file size.
_NOISE = {
    "score": (re.compile(r"(?:\bby|spread|odds|record(?: of)?|improv\w*(?: to)?|moves? to|lead(?:s|ing)?(?: \w+)?(?: by)?|trail(?:s|ing)?(?: \w+)?(?: by)?|half-?time|series)\s*[:\-]?\s*$", re.I),
              re.compile(r"^\s*(?:record|start|streak|lead|after (?:the )?(?:first|second|third|fourth|1st|2nd|3rd|4th)\b|after (?:a |the )?(?:win|loss|victory|defeat)|"
                         r"at (?:the )?half|at halftime|(?:in|through) the (?:first|second|third|fourth)|halftime|season|overall|in conference|"
                         r"points?\b|pts\b|games?\b|runs?\b)", re.I)),
    "money": (re.compile(r"(?:\bup|\bdown|(?:up|down|rose|fell|climbed|dropped) from|\bby|\bwas|\bthan|\bversus|\bvs\.?|previously|gained?|lost|rose|fell|plus|minus|\+|-|−|gain of|loss of|change of|increase of|decrease of)\s*$", re.I),
              re.compile(r"^\s*(?:on the day|higher|lower|more|less|gain|loss|drop|increase|decrease|change|a day|last (?:week|month|year)|ago|earlier|previously)", re.I)),
    "version": (re.compile(r"\$\s*$"), re.compile(r"^\s*(?:mb|gb|kb|tb|stars?|million|billion|%|percent|ghz|mhz|fps|ms)\b", re.I)),
    "count": (re.compile(r"(?:\d:|\$\s*|" + _MONTH_RX + r"\.?\s*)$", re.I), re.compile(r"^\s*(?:stars?\b|%|percent|\u00b0|degrees|mph|a\.?m\b|p\.?m\b|:\d)", re.I)),
}


def _num(text: str) -> tuple[float, int]:
    """(value, significant digits) of a printed number: '8,848.86' -> (8848.86, 6), '8,849' -> (8849, 4), '235,000' -> (235000, 3),
    '$98.4k' -> (98400, 3), '2.5 million' -> (2500000, 2)."""
    short = re.search(r"\d\s?([MBKmbk])$", text.strip())            # 330M, 1.2B, $5 B, $1.2m
    t = text.lower().replace("$", "").replace(",", "").strip()
    mult = 1.0
    if short:
        mult, t = {"M": 1e6, "B": 1e9, "K": 1e3}[short.group(1).upper()], t[:-1]
    for word, m in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("thousand", 1e3)):
        if word in t:
            mult, t = m, t.replace(word, "")
    if t.endswith("k"):
        mult, t = 1e3, t[:-1]
    t = t.strip()
    digits = t.replace(".", "").lstrip("0")
    return float(t) * mult, max(1, len(digits))                # 20 has two significant digits: it is 20, not "about 20 to 30"


def _round_sig(value: float, n: int) -> float:
    return 0.0 if value == 0 else round(value, n - 1 - math.floor(math.log10(abs(value))))


def _temp_label(text: str, start: int, end: int) -> str:
    """What a temperature is the temperature OF: the day's high, the night's low, or the reading now. Only like is compared with like."""
    before = text[max(0, start - 24):start].lower()
    last = None
    for mm in re.finditer(r"\b(high|highs|max|maximum|top|low|lows|min|minimum|overnight)\b", before):
        last = mm
    if last and len(before) - last.end() <= 14:
        return "high" if last.group(1) in ("high", "highs", "max", "maximum", "top") else "low"
    m = re.match(r"\s*(high|low)\b", text[end:end + 12].lower())
    return m.group(1) if m else "now"


def _positions(kind, text) -> list[tuple[object, str, int, int]]:
    out = []
    if kind == "score":
        for m in list(_SCORE.finditer(text)) + list(_SCORE_NAMES.finditer(text)):
            hi, lo = sorted((int(m.group(1)), int(m.group(2))), reverse=True)
            out.append(((lo, hi), f"{hi}-{lo}", m.start(), m.end()))                  # shown winner first, whichever way the page wrote it
    elif kind == "version":
        for m in _VERSION.finditer(text):
            parts = tuple(int(p) for p in m.group(1).split("."))
            out.append((parts, m.group(1), m.start(), m.end()))
    elif kind == "money":
        found = list(_MONEY.finditer(text))
        group: dict[int, int] = {}
        for i in range(len(found) - 1):                       # "$799 to $1,199", "$10 to $15", "between $30 and $40": one range
            if _RANGE_CONNECT.match(text[found[i].end():found[i + 1].start()]):
                group[i + 1] = group[i] = group.get(i, i)
        for i, m in enumerate(found):
            out.append(((*_num(m.group(0)), group.get(i)), m.group(0).strip(), m.start(), m.end()))
    elif kind == "percent":
        for m in _PERCENT.finditer(text):
            out.append((re.sub(r"\s|percent", "", m.group(0), flags=re.I).rstrip("%"), m.group(0).strip(), m.start(), m.end()))
    elif kind == "temp":
        for m in _TEMP.finditer(text):
            unit = (m.group(2) or m.group(3) or "F").upper()[0]
            value = int(m.group(1))
            fahrenheit = value * 9 / 5 + 32 if unit == "C" else value
            out.append(((_temp_label(text, m.start(), m.end()), float(fahrenheit)), m.group(0).strip(), m.start(), m.end()))
    elif kind == "count":
        for m in _COUNT.finditer(text):
            shown = m.group(0).strip()
            if (len(re.sub(r"\D", "", shown)) >= 2 or re.search(r"(?i)million|billion|thousand|trillion|\d[MBK]$", shown)) and not re.fullmatch(r"(?:18|19|20)\d\d", shown):          # a year is not a count
                value, sig = _num(shown)
                mantissa = re.sub(r"[^\d.]", "", re.sub(r"(?i)\s*(?:million|billion|thousand|trillion)|[MBK]$", "", shown))
                digits = mantissa.replace(".", "").lstrip("0")
                trim = max(1, len(digits.rstrip("0")))
                magnitude = bool(re.search(r"(?i)million|billion|thousand|trillion|\d[MBK]$", shown))
                rounded = digits != digits.rstrip("0") and (value >= 1000 or magnitude)       # 8,300,000 and 330 million look rounded; 20 and 100 do not
                point = re.search(r"\.(\d+)", mantissa)
                decimals = len(point.group(1)) if point else 0
                unit = _UNIT_AFTER.match(text[m.end():m.end() + 14])
                if unit and unit.group(1).lower() in _UNITS:
                    dim, factor = _UNITS[unit.group(1).lower()]
                    out.append(((value * factor, sig, dim, decimals, factor, trim, rounded), f"{shown} {unit.group(1)}",
                                m.start(), m.start() + len(m.group(0)) + len(unit.group(0))))
                else:
                    out.append(((value, sig, None, decimals, 1.0, trim, rounded), shown, m.start(), m.end()))
    elif kind == "date":
        for m in _DATE_FIG.finditer(text):
            mon = (m.group(1) or m.group(5) or "")[:3].lower()
            out.append(((mon, int(m.group(2) or m.group(4))), m.group(0).strip(), m.start(), m.end()))
    return sorted(out, key=lambda f: f[2])


_OLDER_AFTER = re.compile(r"^\s*(?:lts\b|reached\b|\(?eol\b|is (?:now )?(?:end of life|eol|deprecated|unsupported|no longer)|was (?:the )?(?:previous|last|old))", re.I)
_OLDER_BEFORE = re.compile(r"(?:before|prior to|older than|previous|legacy|since|until|up to|replaces?|replaced)\s*$", re.I)


_TO_NEW = re.compile(r"^\s*(?:[\w']+\s+){0,3}?(?:up to|down to|to|->|\u2192)\s*\$\s?\d", re.I)
_ARROW = re.compile(r"^\s*(?:->|\u2192)\s*\$")
_CHANGE_DIRECT = re.compile(r"\b(?:went|gone|increased?|rose|risen|rises?|fell|falls?|fallen|dropped?|climbed?|jumped?|surged?|moved?|moves|up|down|"
                            r"slid|slipped|changed?|grown|grew|soared|plunged|plummeted|rocketed|ticked|edged|dipped|shifted|rallied|gained|lost)\b(?:\W+\w+){0,2}\W*$", re.I)
# A range says so: "range", "between", "costs from", "starts at", "depending", "per month"... Without such a cue, "from $X to $Y" is a CHANGE: X is the old value
# (a wrong "old value" guess only gives a conflict, a wrong "range" guess gives a false agreement).
_RANGE_CUE = re.compile(r"\b(?:range[sd]?|ranging|between|starts? at|starting at|starts? from|starting from|(?:costs|prices|plans|tickets|priced|pricing|flights|rooms|rates|fees)\s+from|"
                        r"anywhere from|depend(?:s|ing)|vary|varies|varying|goes? for|runs? from|run from|(?:per|a|each)\s+(?:month|year|night|person|ticket|week|day)|each|monthly|annually|apiece)\b", re.I)


def _old_price(text: str, start: int, end: int) -> bool:
    """Is the figure at text[start:end] the OLD price of a change ("reduced the price from $999 to $799" -> $999; an arrow always)?"""
    if _ARROW.search(text[end:end + 12]):
        return True
    if not _TO_NEW.search(text[end:end + 40]):
        return False
    before = text[max(0, start - 50):start]
    if re.search(r"\bfrom\W*$", before, re.I):
        return not _RANGE_CUE.search(text)
    return bool(_CHANGE_DIRECT.search(before))
_RANGE_CONNECT = re.compile(r"^[,;]?\s*(?:[\w']+\s+){0,2}?(?:up to|down to|to|and|through|-|\u2013|\u2014)\s*$", re.I)


def figures(kind, text, latest: bool = False) -> list[tuple[object, str]]:
    """[(comparison key, text as printed)] for one kind of figure, in the order the text prints them, without the figures that
    are not the answer (a record "5-2 start", a "lead 30-28 after the first quarter", "up $500 on the day", "11.3 MB")."""
    text = _s(text)
    before_rx, after_rx = _NOISE.get(kind, (None, None))
    out = []
    for key, shown, start, end in _positions(kind, text):
        if before_rx and before_rx.search(text[max(0, start - 22):start]):
            continue
        if after_rx and after_rx.search(text[end:end + 45]):
            continue
        if kind == "money" and _old_price(text, start, end):
            continue                                          # "from $95,000 to $98,400": the old price; the one after "to" is the price (unless a range cue)
        if latest and kind == "version" and (_OLDER_BEFORE.search(text[max(0, start - 18):start]) or _OLDER_AFTER.search(text[end:end + 40])):
            continue                                          # asked for the latest: "3.9 reached end of life", "4.5 LTS" are not it
        out.append((key, shown))
    return out


def _same(kind, a, b) -> bool:
    """Do two figures say the same thing? Versions on the same release line (3.14 / 3.14.1), temperatures within two degrees
    once Celsius and Fahrenheit are made the same, everything else exactly."""
    if kind == "version" and isinstance(a, tuple) and isinstance(b, tuple):
        n = min(len(a), len(b))
        return a[:n] == b[:n]
    if kind == "temp":
        return a[0] == b[0] and abs(a[1] - b[1]) <= 2                          # a high is not a low is not the reading now
    if kind in ("count", "money") and isinstance(a, tuple) and isinstance(b, tuple):
        if kind == "money":                                   # a price a hair apart (1%) is the same price
            return max(a[0], b[0]) > 0 and abs(a[0] - b[0]) / max(a[0], b[0]) <= 0.01
        da, db = (a[2] if len(a) > 2 else None), (b[2] if len(b) > 2 else None)
        if da and db:                                         # 8,849 metres and 29,032 feet: the same length, once both are in metres
            if da != db:
                return False
            tolerance = 0.005 if a[4] == b[4] else 0.01        # half a percent; a converted unit rounds, so one percent
            if a[3] != b[3] and min(a[3], b[3]) > 0:          # figures printed to different decimals: half the coarser one's last digit, once
                coarse = a if a[3] < b[3] else b
                tolerance += 0.5 * 10 ** -coarse[3] * coarse[4] / max(abs(coarse[0]), 1e-9)
            return abs(a[0] - b[0]) / max(abs(a[0]), abs(b[0]), 1e-9) <= tolerance
        # A round figure (8,300,000; 330 million) is rounded to its last non-zero digit when set against a longer one; short round
        # figures (20, 100) and two figures of the same length are compared exactly.
        pa = a[5] if len(a) > 6 and a[6] and b[1] >= 3 else a[1]
        pb = b[5] if len(b) > 6 and b[6] and a[1] >= 3 else b[1]
        n = min(pa, pb)
        if _round_sig(a[0], n) != _round_sig(b[0], n):
            return False
        if n < max(a[1], b[1]):                               # one was rounded to meet the other: they must also be close (1,000 is not 1,499)
            return abs(a[0] - b[0]) / max(abs(a[0]), abs(b[0]), 1e-9) <= (0.05 if n >= 2 else 0.03)       # (1.5 million may be 1,547,382)
        return True
    return a == b


def wanted_kinds(question) -> list[str]:
    q = _s(question)
    kinds = []
    if re.search(r"\b(?:score|won|win|wins|winner|beat|game|match|final|lost|result)\b", q, re.I):
        kinds.append("score")
    if re.search(r"\b(?:version|driver|release|update|build|patch|latest|newest)\b", q, re.I):
        kinds.append("version")
    if re.search(r"\b(?:price|cost|costs|worth|stock|bitcoin|how much|salary|exchange rate)\b", q, re.I):
        kinds.append("money")
    if re.search(r"\b(?:weather|temperature|forecast|degrees|hot|cold|high|low)\b", q, re.I):
        kinds.append("temp")
    if re.search(r"%|\b(?:percent|rates?|inflation|unemployment)\b", q, re.I):
        kinds.append("percent")
    if re.search(r"\b(?:population|how many|how tall|how big|how long|how far|how fast|how heavy|how deep|how wide|how high)\b", q, re.I):
        kinds.append("count")
    if re.search(r"\b(?:when|date|release date|born|died|founded|launch(?:ed|es)?)\b", q, re.I):
        kinds.append("date")
    return kinds


def row_text(row) -> str:
    if not isinstance(row, dict):
        return ""
    facts = row.get("facts")
    facts = [facts] if isinstance(facts, str) else (list(facts) if isinstance(facts, (list, tuple)) else [])
    return " ".join(_s(x) for x in ([row.get("title")] + facts + [row.get("excerpt") or row.get("snippet")]) if x)


# -- the cross-check ----------------------------------------------------------------------------------------------------------
_QSTOP = {"who", "what", "when", "where", "the", "and", "for", "how", "did", "does", "was", "were", "are", "last", "night", "latest", "newest",
          "current", "today", "tomorrow", "yesterday", "tonight", "this", "that", "with", "from", "about", "much", "many"}


def _qwords(question) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", _s(question).lower())} - _QSTOP


_BACK_REF = re.compile(r"\b(?:earlier|previous(?:ly)?|last (?:meeting|time|season|year|week|month)|in their (?:last|previous|earlier)|had (?:won|beaten)|ago)\b", re.I)


def _candidates(kind, row, question) -> list[tuple[object, str]]:
    """The figures of one result that could answer the question, the most relevant sentence first: the first one is that
    result's headline figure. (Not any number it prints: a shared "up $500" is not agreement on the price.)"""
    words = _qwords(question)
    sentences = split_sentences(row_text(row))
    if kind == "version" and re.search(r"\b(?:latest|newest|current|most recent)\b", _s(question), re.I):
        items = []                                            # cue words first ("latest", "download"), then the highest version
        for sentence in sentences:
            cue = bool(re.search(r"\b(?:latest|newest|current|stable|most recent|download|available|released)\b", sentence, re.I))
            items.extend((cue, key, shown) for key, shown in figures(kind, sentence, latest=True))
        out = []
        for _, key, shown in sorted(items, key=lambda it: (it[0], it[1]), reverse=True):
            if not any(key == o[0] for o in out):
                out.append((key, shown))
        return out
    if kind == "date":                                        # "when was X born": only the sentence that says born, not another date on the page
        triggers = re.findall(r"\b(born|died|founded|launch\w*|release\w*|built|opened|married|started|ended|created)\b", _s(question).lower())
        said = [x for x in sentences if any(t[:5] in x.lower() for t in triggers)] if triggers else []
        sentences = said or sentences
    ranked = sorted(enumerate(sentences), key=lambda it: (bool(_BACK_REF.search(it[1])), -len(words & set(re.findall(r"[a-z0-9]+", it[1].lower()))), it[0]))
    out: list[tuple[object, str]] = []
    for _, sentence in ranked:
        for fig in figures(kind, sentence):
            if not any(_same(kind, fig[0], o[0]) and fig[0] == o[0] for o in out):
                out.append(fig)
    return out


_COPY_STOP = {"ap", "reuters", "via", "the", "a", "an", "source", "photo", "file"}


def _tokens(row) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", row_text(row).lower()) if t not in _COPY_STOP}


def _copy(a: set, b: set) -> bool:
    """Is one text a copy of the other (a wire story on two sites)? The same words exactly (five or more), or 85% the same
    words in a text of ten or more. Two short snippets in different words are two sources."""
    if len(a) < 5 or len(b) < 5:
        return False
    if a == b:
        return True
    return len(a) >= 10 and len(b) >= 10 and len(a & b) / len(a | b) >= 0.85


def _sources(rows: list[dict]) -> list[list[dict]]:
    """Independent sources: results of one site, and near-identical copies of one story (a wire article on three sites), are
    one source."""
    groups: list[list[dict]] = []
    for r in rows:
        tok = _tokens(r)
        for g in groups:
            same_site = site_of(g[0].get("url")) == site_of(r.get("url"))
            similar = any(_copy(tok, _tokens(m)) for m in g)
            if same_site or similar:
                g.append(r)
                break
        else:
            groups.append([r])
    return groups


def _ends(kind, cands) -> list:
    """The figures a source's headline stands for: a price range ("from $799 to $1,199") is both its ends; anything else, the one figure."""
    first = cands[0][0]
    if kind == "money" and isinstance(first, tuple) and len(first) > 2 and first[2] is not None:
        return [c[0] for c in cands if isinstance(c[0], tuple) and len(c[0]) > 2 and c[0][2] == first[2]]
    return [first]


def _blank(window=None) -> dict:
    return {"status": "none", "rows": 0, "sites": [], "kind": None, "agreed": None, "agree_sites": [], "conflict": [], "dissent": [],
            "copies": {}, "newest": None, "age": None, "window": window, "dated": 0, "usable": []}


def assess(pack, question=None, now: date | None = None) -> dict:
    """Is what the results say settled? status: none | stale | single | agree | conflict | multi | error.
      none      no usable result
      stale     every result is older than the question allows
      single    one independent source only (or only one printed the figure asked for): say so, do not state it as settled
      agree     two or more independent sources print the same answer figure
      conflict  independent sources print different answer figures: say "I found conflicting reports"
      multi     several sources, but nothing numeric to compare
      error     the cross-check itself failed on a strange result: treat as unconfirmed
    Never raises."""
    now = now or today()
    try:
        return _assess(pack, question, now)
    except Exception:                                         # noqa: BLE001 - a bad row must not lose the answer
        info = _blank()
        info["status"] = "error"
        return info


def _assess(pack, question, now) -> dict:
    if not isinstance(pack, dict):
        return _blank()
    question = _s(question or pack.get("question") or pack.get("query"))
    rows = rows_of(pack)
    window = window_days(question, now)
    info = _blank(window)
    if not rows:
        return info
    dates = [d for d in (row_date(r, now)[0] for r in rows) if d]
    info["dated"] = len(dates)
    if dates:
        info["newest"] = max(dates)
        info["age"] = (now - date.fromisoformat(info["newest"])).days
    info["rows"] = len(rows)
    fresh = [r for r in rows if not (window and _age(r, now) is not None and _age(r, now) > window)]
    if not fresh:
        info["status"] = "stale"
        info["sites"] = sorted({site_of(r.get("url")) for r in rows})
        return info
    info["usable"] = fresh
    groups = _sources(fresh)
    leads = [site_of(g[0].get("url")) for g in groups]
    info["sites"] = leads
    info["copies"] = {site_of(g[0].get("url")): sorted({site_of(m.get("url")) for m in g[1:]} - {site_of(g[0].get("url"))}) for g in groups if len(g) > 1}
    for kind in wanted_kinds(question):
        per = {}
        for g, lead in zip(groups, leads):
            cands = []
            for r in g:
                cands.extend(c for c in _candidates(kind, r, question) if not any(c[0] == o[0] for o in cands))
            if cands:
                per[lead] = cands
        if not per:
            continue
        info["kind"] = kind
        if len(per) == 1:
            lead, cands = next(iter(per.items()))
            info.update(status="single", agree_sites=[lead], agreed=cands[0][1], quote=cands[0][0], quote_site=lead)
            return info
        best, best_support = None, 0
        for lead, cands in per.items():                       # the headline figure most sources back
            support = [o for o, oc in per.items() if o == lead or any(_same(kind, x, y) for x in _ends(kind, cands) for y in _ends(kind, oc))
                       or (kind == "temp" and any(_same(kind, cands[0][0], c[0]) for c in oc))]       # (a temperature of the same label, wherever it is)
            if len(support) > best_support:
                best, best_support = (lead, support), len(support)
        lead, support = best
        if best_support >= 2:
            shown = [per[s][0][1] for s in support]
            spread = len(set(shown)) > 1
            if kind == "money":                               # "$2.5M" and "$2.5 million" are the same figure written twice
                values = [per[s][0][0][0] for s in support]
                spread = (max(values) - min(values)) > 1e-9 * max(values)
            if kind == "money" and spread:                    # "about $98,412 to $98,430"
                ordered = sorted((per[s][0][0][0], per[s][0][1]) for s in support)
                agreed = f"{ordered[0][1]} to {ordered[-1][1]}"
            else:
                agreed = min(shown, key=len) if kind == "version" else per[lead][0][1]
            info.update(status="agree", agree_sites=support, agreed=agreed, approx=spread and kind in ("temp", "money"),
                        dissent=[(s, c[0][1]) for s, c in per.items() if s not in support], quote=per[lead][0][0], quote_site=lead)
            return info
        if kind == "temp":                                    # only like with like: a high with a high, a reading with a reading
            labels: dict = {}
            for site, cands in per.items():
                for (label, _), shown_ in cands:
                    labels.setdefault(label, {}).setdefault(site, shown_)
            shared = [label for label, d in labels.items() if len(d) >= 2]
            if not shared:
                site = next(iter(per))
                info.update(status="single", agree_sites=[site], agreed=per[site][0][1], incomparable=True, quote=per[site][0][0], quote_site=site)
                return info
            same_label = labels[shared[0]]
            site = next(iter(same_label))
            first = next(c[0] for c in per[site] if c[0][0] == shared[0])
            info.update(status="conflict", conflict=list(same_label.items()), agree_sites=list(same_label), quote=first, quote_site=site)
            return info
        if kind == "count":
            dims = {c[0][0][2] for c in per.values() if len(c[0][0]) > 2}
            if len(dims) > 1:
                site = next(iter(per))
                info.update(status="single", agree_sites=[site], agreed=per[site][0][1], incomparable="units", quote=per[site][0][0], quote_site=site)
                return info
        if kind == "money":
            values = [(per[s][0][0][0], s, per[s][0][1]) for s in per]
            low, high = min(v[0] for v in values), max(v[0] for v in values)
            if high > 0 and (high - low) / high <= 0.03:      # 1-3% apart: close, not the same, not a conflict
                info.update(status="close", conflict=[(s, f) for _, s, f in values], agree_sites=list(per), quote=per[next(iter(per))][0][0],
                            quote_site=next(iter(per)))
                return info
        first_site = next(iter(per))
        info.update(status="conflict", conflict=[(s, c[0][1]) for s, c in per.items()], agree_sites=list(per), quote=per[first_site][0][0], quote_site=first_site)
        return info
    info["status"] = "multi" if len(groups) >= 2 else "single"
    return info


def _names(sites) -> str:
    return " and ".join(sites) if len(sites) <= 2 else ", ".join(sites[:-1]) + " and " + sites[-1]


def verdict(pack, question=None, now: date | None = None) -> str:
    """The cross-check as one or two plain sentences (what a person is told, and what the model must say)."""
    now = now or today()
    question = _s(question or (pack.get("question") or pack.get("query") if isinstance(pack, dict) else ""))
    a = assess(pack, question, now)
    s = a["status"]
    age = ""
    if a["newest"] and a["window"] and a["age"] is not None and a["age"] > a["window"] and s != "stale":
        age = f" The newest dated one is from {say_date(a['newest'], now)}, so it may be out of date."
    if s == "error":
        return "I couldn't cross-check those results, so treat them as unconfirmed."
    if s == "none":
        return "I found nothing reliable on that, so I won't guess."
    if s == "stale":
        return (f"The results I found are old (the newest is from {say_date(a['newest'], now)}), so I can't confirm the current answer."
                if a["newest"] else "I couldn't find a reliable, current source for that.")
    if s == "conflict":
        parts = "; ".join(f"{site} says {fig}" for site, fig in a["conflict"][:3])
        return f"I found conflicting reports: {parts}. I can't tell you which is right." + age
    if s == "close":
        parts = "; ".join(f"{site} says {fig}" for site, fig in a["conflict"][:3])
        return f"The sources give close but not the same figure ({parts}); treat it as approximate." + age
    if s == "agree":
        extra = "".join(f" ({site} says {fig}.)" for site, fig in a["dissent"][:2])
        return f"{_names(a['agree_sites'][:3])} agree on {'about ' if a.get('approx') else ''}{a['agreed']}." + extra + age
    if s == "single":
        if a.get("incomparable") == "units":
            return (f"The sources use different units, so I can't compare them like for like; treat {a['agree_sites'][0]}'s {a['agreed']} as unconfirmed." + age)
        if a.get("incomparable"):
            return (f"I can't compare them like for like (they report different things, such as a reading now and the day's high), "
                    f"so treat {a['agree_sites'][0]}'s {a['agreed']} as unconfirmed." + age)
        if a["kind"]:
            site = a["agree_sites"][0]
            carrier = next((r for r in a["usable"] if site_of(r.get("url")) == site), {})
            if is_primary(carrier, question):
                return f"That is from {site} itself, the primary source; I found no second source to confirm it." + age
            return f"Only {site} gives that figure, so treat it as unconfirmed." + age
        if a["sites"] and is_primary(a["usable"][0] if a["usable"] else {}, question):
            return f"That is from {a['sites'][0]} itself, the primary source; I found no second source to confirm it." + age
        return f"That is from one source only ({a['sites'][0]}), so treat it as unconfirmed." + age
    nodate = " None of them states a date." if a["window"] and not a["dated"] else ""
    return f"I found {len(a['sites'])} sources but nothing numeric to cross-check, so this is what they say, not a confirmed fact." + nodate + age


def evidence_note(pack, question=None, now: date | None = None) -> str:
    """Lines appended to the evidence the model reads: the computed cross-check and how it must be spoken."""
    now = now or today()
    question = _s(question or (pack.get("question") or pack.get("query") if isinstance(pack, dict) else ""))
    a = assess(pack, question, now)
    copies = "; ".join(f"{k} also carried by {', '.join(v)}" for k, v in a["copies"].items())
    lines = [f"Cross-check (computed in code, not from the pages; today is {now.isoformat()}): "
             f"{a['rows']} result{'s' if a['rows'] != 1 else ''} from {len(a['sites'])} site{'s' if len(a['sites']) != 1 else ''}"
             f"{' (' + ', '.join(a['sites'][:5]) + ')' if a['sites'] else ''}{'; ' + copies if copies else ''}."]
    lines.append("Verdict: " + verdict(pack, question, now))
    if a["status"] != "agree":
        lines.append("Your answer MUST carry that caveat (your own words, same meaning) and must not offer to do anything further.")
    if a["window"] and not a["dated"] and a["status"] not in ("none",):
        lines.append("No result states a date: say you cannot tell how recent this is.")
    return "\n".join(lines)


# -- the spoken answer -----------------------------------------------------------------------------------------------------------
def best_sentence(text, question="", kinds=None) -> str:
    """The sentence of `text` that best answers the question, exactly as printed: overlap with the question's words, and a
    figure of the kind asked for. '' when nothing matches. Never composed or shortened mid-word."""
    words = _qwords(question)
    kinds = kinds if kinds is not None else wanted_kinds(question)
    best, best_score = "", 0.0
    for i, s in enumerate(split_sentences(" ".join(_s(text).split()))):
        s = s.strip().rstrip(".").strip()
        if not 20 <= len(s) <= 300:
            continue
        toks = set(re.findall(r"[a-z0-9]+", s.lower()))
        score = 2.0 * sum(1 for w in words if w in toks) + (2.0 if any(figures(k, s) for k in kinds) else (0.3 if re.search(r"\d", s) else 0)) - 0.05 * i
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 1.5 else ""


def quoted_row(pack, question=None, now: date | None = None) -> dict | None:
    """The result `answer()` quotes: the first usable (not out of date) one with a sentence that answers the question, else
    the first usable, else the first result."""
    now = now or today()
    question = _s(question or (pack.get("question") or pack.get("query") if isinstance(pack, dict) else ""))
    a = assess(pack, question, now)
    carriers = [r for r in a["usable"] if site_of(r.get("url")) in a["agree_sites"]]
    for r in carriers + [r for r in a["usable"] if r not in carriers]:
        if best_sentence(row_text({**r, "title": ""}), question):
            return r
    if a["usable"]:
        return a["usable"][0]
    rows = rows_of(pack)
    return rows[0] if rows else None


def _sentence_with(row, kind, key, question) -> str:
    """The sentence of a result that prints the figure `key` (so what is quoted and what the verdict says are the same figure)."""
    latest = bool(re.search(r"\b(?:latest|newest|current|most recent)\b", _s(question), re.I))
    for sentence in split_sentences(row_text({**row, "title": ""})):
        if any(_same(kind, k, key) for k, _ in figures(kind, sentence, latest=latest)):
            return sentence.strip().rstrip(".").strip()
    return ""


def answer(pack, question=None, now: date | None = None) -> str:
    """2-4 spoken sentences from the results alone, with source and date: the best-matching sentence quoted as the top usable
    result printed it, then the cross-check (agree, single source, conflicting reports, out of date) or the honest
    "found nothing reliable"."""
    now = now or today()
    pack = pack if isinstance(pack, dict) else {}
    question = _s(question or pack.get("question") or pack.get("query"))
    a = assess(pack, question, now)
    label = _s(pack.get("question") or pack.get("query") or question)
    if a["status"] == "none":
        return f"I searched for {label} and found nothing reliable, so I won't guess."
    out = []
    if a["status"] not in ("stale", "error"):
        if a.get("quote") is not None:                        # the verdict is about THIS figure: quote the sentence that prints it
            candidates = [(r, _sentence_with(r, a["kind"], a["quote"], question)) for r in a["usable"] if site_of(r.get("url")) == a.get("quote_site")]
        else:
            candidates = [(r, best_sentence(row_text({**r, "title": ""}), question)) for r in a["usable"]]
        for r, sentence in candidates:
            if sentence:
                iso, how = row_date(r, now)
                dated = f", {say_date(iso, now)}" if iso and how != "url" else ""
                out.append(f"{r.get('site') or site_of(r.get('url'))}{dated}, says: {sentence}.")
                break
    out.append(verdict(pack, question, now))
    return " ".join(out)


# -- follow-ups on the last search --------------------------------------------------------------------------------------------------
_ORD = {"first": 0, "1st": 0, "top": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1,
        "one": 0, "two": 1, "three": 2, "four": 3, "five": 4, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4}
_NOUN = r"(?:one|result|source|link|story|article|item|page|site)"
_ORD_REF = re.compile(rf"\b(?:the |that )?(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|last|top) {_NOUN}\b|"
                      rf"\b(?:number|result|source|link|option) (one|two|three|four|five|[1-5])\b", re.I)
_SITE_REF = re.compile(rf"\bthe ([a-z][a-z0-9-]{{2,}}) {_NOUN}\b", re.I)
_VERB = re.compile(r"\b(?:tell me more|more (?:about|on|from|of)|read|open|show|go (?:deeper|into)|dig (?:deeper )?into|expand|what does|what did|"
                   r"what do|summari[sz]e|details?|look at|check|pull up|explain)\b", re.I)
_MORE = re.compile(r"^\W*(?:(?:ok|okay|and|so|yes|yeah|please|jarvis|can you|could you)\W+)*(?:tell me more|more details?|more (?:on|about) (?:that|this|it)|"
                   r"go deeper|dig deeper|can you dig deeper|give me more (?:details?|information|info)|and\??|keep going)\W*$", re.I)
_SOURCE_Q = re.compile(r"\b(?:what(?:'s| is| was) (?:the |your )?source(?=\W*$| of (?:that|this|it|your answer|that information)\W*$)|where did you (?:get|find|read|see) (?:that|this|it)|which (?:site|source|website) "
                       r"(?:is|was) (?:that|this)(?: from)?|who (?:said|reported|says|reports) (?:that|this)|how do you know (?:that|this)|"
                       r"is that (?:from|on) (?:a |the )?(?:real|reliable|good) (?:source|site)|can you (?:cite|source) (?:that|it))\b|"
                       r"^\W*(?:and )?are you (?:sure|certain)(?: (?:about )?(?:that|this|it))?\W*$", re.I)
_WHEN_WORD = re.compile(r"\b(?:the day after tomorrow|tomorrow(?: (?:morning|afternoon|evening|night))?|yesterday|last night|today|tonight|"
                        r"this (?:weekend|week|morning|evening)|next (?:week|weekend|month)|last (?:week|month)|"
                        r"(?:on |this |next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", re.I)
_WHEN_ASK = re.compile(r"^\W*(?:(?:ok|okay|and|so|then|well|jarvis|yes|yeah)\W+)*(?:what about|how about|and what about|what(?:'s| is) it(?: like)?(?: for| on)?|"
                       r"how(?:'s| is) it(?: for| on)?|and (?:for|on)|for|on)?\W*(?:the )?$", re.I)
_PLACE_ASK = re.compile(r"^\W*(?i:(?:(?:ok|okay|and|so|then|well|jarvis)\W+)*(?:what about|how about|and what about|and how about|and)\W+)"
                        r"(?P<prep>(?i:in|for|at|near)\s+)?(?P<place>[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2})\W*$")
_PLACES = set("""springfield norfolk decatur virginia beach chesapeake portsmouth suffolk richmond williamsburg jacksonville chatham smithfield
 roanoke arlington alexandria charlottesville fredericksburg lynchburg blacksburg washington baltimore philadelphia new york boston chicago
 atlanta miami orlando tampa jacksonville charlotte raleigh durham nashville memphis dallas houston austin san antonio denver phoenix las vegas
 seattle portland sacramento san francisco los angeles san diego detroit cleveland columbus cincinnati pittsburgh minneapolis milwaukee st louis
 kansas city oklahoma city salt lake city honolulu anchorage toronto vancouver montreal london paris berlin madrid rome tokyo beijing shanghai
 seoul delhi mumbai sydney melbourne mexico city havana kingston port-au-prince cairo lagos nairobi moscow dubai istanbul amsterdam dublin
 virginia maryland carolina georgia florida texas california ohio michigan illinois pennsylvania tennessee kentucky alabama nevada arizona colorado
 oregon utah hawaii alaska canada mexico france spain italy germany japan china india brazil england scotland ireland haiti jamaica cuba""".split())


def _looks_like_place(place: str, prep) -> bool:
    """'and Boston?' and 'how about in Smithfield' swap a place; 'what about Python' or 'what about Tom' is a new question."""
    words = place.lower().split()
    known = place.lower() in _PLACES or all(w in _PLACES for w in words) or (len(words) > 1 and words[0] in _PLACES)
    return known or bool(prep and not _NOT_PLACES & set(words))


_NOT_PLACES = {"python", "java", "javascript", "apple", "google", "microsoft", "windows", "linux", "blender", "tesla", "nvidia", "amazon", "netflix",
               "lakers", "celtics", "yankees", "bitcoin", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow",
               "today", "tonight", "yesterday", "january", "february", "march", "april", "june", "july", "august", "september", "october",
               "november", "december", "english", "french", "spanish", "german", "chrome", "edge", "firefox", "android", "iphone"}
_PLACE_IN = re.compile(r"\b(?:in|for|at|near) [A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}")


def _ordinal(text, rows) -> int | None:
    m = _ORD_REF.search(text)
    if m:
        return _ORD[(m.group(1) or m.group(2)).lower()]
    m = _SITE_REF.search(text)
    if m:
        name = m.group(1).lower()
        for i, r in enumerate(rows):
            if site_of(r.get("url")).split(".")[0] == name or name in _s(r.get("site")).lower().split("."):
                return i
    return None


def _source_line(pack, now) -> str:
    row = quoted_row(pack, None, now)
    if not row:
        return ""
    iso, how = row_date(row, now)
    when = f", dated {say_date(iso, now)}" if iso and how != "url" else ", with no date stated"
    return f"{row.get('site') or site_of(row.get('url'))}{when}: {row['url']}"


def followup(text, pack, now: date | None = None, veto=None, extra=None) -> dict | None:
    """What a short follow-up asks of the search just made, or None when it is something else. Result: {"say": text} (answered
    from the pack), or {"skill": name, "args": {...}}. `extra`: every pack of the last turn (a line that asked two things).
    The Brain only asks this straight after a web answer."""
    now = now or today()
    t = " ".join(_s(text).replace("’", "'").split())
    if not t or len(t.split()) > 14 or not isinstance(pack, dict) or not pack:
        return None
    rows = rows_of(pack)
    question = _s(pack.get("question") or pack.get("query"))
    topic = base_of(question)
    if _SOURCE_Q.search(t):
        packs = [p for p in (extra or []) if isinstance(p, dict)] or [pack]
        if not any(rows_of(p) for p in packs):
            return {"say": "I had no usable sources for that, so there is nothing to cite."}
        if len(packs) > 1:
            lines = [f"{_source_line(p, now)} for {base_of(p.get('question') or p.get('query'))}" for p in packs if rows_of(p)]
            return {"say": "I used " + "; ".join(lines) + ". I haven't checked anything beyond those results."}
        top = quoted_row(pack, None, now)
        others = [site_of(r.get("url")) for r in rows[:4] if r is not top and site_of(r.get("url")) != site_of(top.get("url"))]
        more = f" I also had {_names(list(dict.fromkeys(others)))}." if others else ""
        return {"say": f"That came from {_source_line(pack, now)}.{more} I haven't checked anything beyond those results."}
    idx = _ordinal(t, rows)
    if idx is not None and _VERB.search(t):
        if not rows:
            return None
        if idx >= len(rows):
            return {"say": f"I only found {len(rows)} result{'s' if len(rows) != 1 else ''}, so there is no number {idx + 1}."}
        return {"skill": "open_web_page", "args": {"url": rows[idx]["url"]}}
    if _MORE.match(t) and topic:
        return {"skill": "research_online", "args": {"question": question, "pages": 4}}
    if topic and _WHEN_WORD.search(t):
        rest = _WHEN_WORD.sub("", t, count=1)
        if _WHEN_ASK.match(rest) and (_WHEN_ASK.match(t[:_WHEN_WORD.search(t).start()]) is not None):
            when = _WHEN_WORD.search(t).group(0)                 # a weekday is resolved by rewrite(), by the tense of the question
            packs = [x for x in (extra or []) if isinstance(x, dict) and rows_of(x)]
            topics = [base_of(_s(x.get("question") or x.get("query"))) for x in packs] if len(packs) > 1 else [topic]
            calls = [("search_internet", {"query": rewrite(f"{re.sub(chr(32) + chr(43), chr(32), _PERIOD.sub('', tp)).strip()} {when}", now),
                                          "objective": "the answer to the question, with the source and its date"}) for tp in topics]
            return {"skill": calls[0][0], "args": calls[0][1], "calls": calls} if len(calls) > 1 else {"skill": calls[0][0], "args": calls[0][1]}
    m = _PLACE_ASK.match(t)
    if m and topic and _PLACE_IN.search(topic) and _looks_like_place(m.group("place"), m.group("prep")) and not (veto and veto(m.group("place"))):
        swapped = _PLACE_IN.sub(lambda mm: mm.group(0).split(" ", 1)[0] + " " + m.group("place"), topic, count=1)
        return {"skill": "search_internet", "args": {"query": rewrite(swapped, now),
                                                     "objective": "the answer to the question, with the source and its date"}}
    return None
