"""Lines that ask for the Creole voice recorder (app/voice-record.html), decided in code before any model and before
the language-lesson route. Whole-line matches only: "record a voice memo", "record my screen", "I recorded my voice
yesterday" and "record this meeting" are not requests for it."""
import re

_LEAD = (r"^\W*(?:(?:hey|ok|okay|so|now|alright)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?(?:please\s+)?"
         r"(?:(?:can|could|would|will) (?:you|we)\s+(?:please\s+)?|i(?: would|'d) like to\s+|i (?:want|need) to\s+|"
         r"i(?:'m| am) (?:ready|going) to\s+|let me\s+|let'?s\s+|help me\s+)?")
_TONGUE = r"(?:(?:own|creole|kreyol|krey[oò]l|haitian|haitian creole)\s+){0,2}"
_RECORD = re.compile(
    _LEAD + r"(?:record|start recording|begin recording)\s+(?:my\s+)?" + _TONGUE + r"(?:voice|creole|kreyol|krey[oò]l)"
    r"(?:\s+(?:for you|for jarvis|now|please|today|in creole|in kreyol))*\W*$", re.I)
_OPEN = re.compile(
    _LEAD + r"(?:open|start|launch|show|bring up|go to|take me to|begin)\s+(?:up\s+)?(?:the |my )?" + _TONGUE +
    r"(?:voice[ -]record(?:er|ing)|record(?:er|ing) page|voice[ -]recording page)(?:\s+page)?(?:\s+(?:now|please))*\W*$", re.I)

LINE = "Opening the recorder. Tap the red button and read each sentence."
PHONE_LINE = LINE + " On the phone: Languages, then Start recording."


def wants(text: str) -> bool:
    t = " ".join((text or "").split())
    return bool(t) and bool(_RECORD.match(t) or _OPEN.match(t))
