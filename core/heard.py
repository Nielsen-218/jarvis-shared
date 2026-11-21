"""What was heard, put right before routing (2026-11-12). Pure text in, text out; it never decides or runs anything.

The speech recogniser makes three kinds of slips that the routers then treat as "not a command" (audit of his real
conversations: about 9 turns plus several refusals, and wrong-target closes):
  1. a bare command in the past tense: "Closed <app>." for "Close <app>." -> the command, when the object is EXACTLY one
     installed app's name and nothing else;
  2. his own name: "Jarrah, ...", "Jervis ..." -> "Jarvis";
  3. app names: "chat g b t", "clawed", "note pad" right after open / close / minimize -> the app's real name, when that app
     is installed.
audio/stt.py already biases the recogniser with the app names (hotwords); this catches what still gets through.

Conservative on purpose (review 2026-11-12: narration must not become a command). "Closed calculator already.", "Closed
the store.", "Opened Discord and it crashed" are talk and stay as they are. No real English word is in the slip table, and
no real first name in the name table. If the app catalogue cannot be read, or is empty, NOTHING is rewritten (fail closed).
normalise() is idempotent.
"""
from __future__ import annotations

import re

_CHANNEL = re.compile(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*")

# 2. His name at the start of the sentence (the address position only). Never a real first name ("Travis": talk to a
# person in the room became a command), never a possessive ("Jarvis's turn").
_NAME = re.compile(r"^(?P<hey>(?:hey|hi|ok|okay|yo)[,\s]+)?(?:jarrah|jervis|jarvas|jarvus|jarves|jarvies|jarbis|javis|garvis|"
                   r"charvis)(?P<after>[,.!?\s]|$)", re.I)

# 3. App names as the recogniser writes them -> the catalogue's name. Only right after a bare open / close / minimize.
_COMMAND_VERB = r"(?:open|opened|close|closed|minimi[sz]e|minimi[sz]ed|focus|maximi[sz]e)"
_APP_SLIPS = (
    (r"chat\s*(?:g\s*b\s*t|g\s*t\s*p|g\s*p\s*d|g\s*b\s*d|t\s*v\s*t|c\s*p\s*t|g\s*p\s*c|g\s+p\s+t)|chad\s*gpt|chat\s*gtp|chat\s+gpt", "ChatGPT"),
    (r"clawed|clawd", "Claude"),
    (r"blendr", "Blender"),
    (r"note\s+pad", "Notepad"),
    (r"disc\s+cord", "Discord"),
    (r"steem", "Steam"),
    (r"spotifi", "Spotify"),
    (r"v\s+s\s+code|vs\s+coat|v\s+s\s+coat", "Visual Studio Code"),
    (r"excell|ex\s+cell", "Excel"),
)
# "Minimize Cloud.", "Minimize Clot.", "Minimize Quad." (his real lines, 2026-11-08..13): Whisper's spellings of Claude that
# are ordinary English words, so only right after an IMPERATIVE verb ("Closed the cloud." is narration: left alone), and only
# when no installed app is called that (Creative Cloud: left alone, see _app).
_IMPERATIVE = r"(?:open|close|minimi[sz]e|focus|maximi[sz]e)"
_WORD_SLIPS = ((r"cloud|clot|quad", "Claude"),)
_SLIP = [(re.compile(r"^(?P<head>(?:(?:hey\s+)?jarvis[,.!\s]+)?(?:please\s+)?" + _IMPERATIVE + r"\s+(?:the\s+|my\s+)?)(?P<app>" + rx +
                     r")(?P<tail>(?:\s+(?:app|window))?[.!?]*)$", re.I), right) for rx, right in _WORD_SLIPS]
_SLIP += [(re.compile(r"^(?P<head>(?:(?:hey\s+)?jarvis[,.!\s]+)?(?:please\s+)?" + _COMMAND_VERB + r"\s+(?:the\s+|my\s+)?)(?P<app>" + rx +
                     r")(?P<tail>(?:\s+(?:app|window))?[.!?]*)$", re.I), right) for rx, right in _APP_SLIPS]

# 1. A bare command heard in the past tense.
_TENSE = {"closed": "Close", "opened": "Open", "minimized": "Minimize", "minimised": "Minimize"}
_PAST = re.compile(r"^(?P<lead>(?:(?:hey\s+)?jarvis[,.!\s]+)?)(?P<verb>closed|opened|minimi[sz]ed)\s+(?P<obj>(?:the\s+|my\s+)?"
                   r"[\w+'-]+(?:\s+[\w+'-]+){0,3}?)(?P<tail>(?:\s+(?:app|window))?[.!]*)$", re.I)


# 0. A command verb said twice ("close close github", his real line 2026-11-06): one verb. The doubled word made the whole
# sentence an unknown app name, and the fuzzy app match closed Logitune (holdout h041).
_TWICE = re.compile(r"^(?P<lead>(?:(?:hey\s+)?jarvis[,.!\s]+)?(?:please\s+)?)(?P<verb>open|close|minimi[sz]e|maximi[sz]e|focus|pause|"
                    r"play|mute|unmute|launch|quit)(?:[\s,.!]+(?P=verb))+(?![\w-])", re.I)


def _once(text: str) -> str:
    return _TWICE.sub(lambda m: m.group("lead") + m.group("verb"), text, count=1)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").casefold())).strip()


def _installed() -> set:
    """The installed catalogue's names, flattened. Raises when it cannot be read: every caller then rewrites nothing."""
    from core import pcsemantic
    return {_flat(n) for n in pcsemantic._catalogue() if _flat(n)}


def exact_app(words: str) -> bool:
    """Are these words EXACTLY one installed app's whole name (optionally 'the' / 'my' in front, 'app' / 'window' after)?
    "calculator", "the steam app", "Microsoft Edge": yes. "the store", "chrome", "calculator already": no."""
    w = re.sub(r"\s+(?:app|window)$", "", re.sub(r"^(?:the|my)\s+", "", _flat(words)))
    return bool(w) and w in _installed()


def _name(text: str) -> str:
    m = _NAME.match(text)
    return (m.group("hey") or "") + "Jarvis" + m.group("after") + text[m.end():] if m else text


def _app(text: str) -> str:
    for rx, right in _SLIP:
        m = rx.match(text)
        if not m:
            continue
        heard_app = m.group("app")
        if _flat(heard_app) == _flat(right) or not exact_app(right):
            return text          # nothing to correct, or the app it would become is not installed
        from core import pcsemantic
        if any(_flat(a) != _flat(right) for a in pcsemantic.mentioned_apps(heard_app)):
            return text          # the words as heard already name something else that is installed: leave them
        word = _flat(heard_app)
        if any(n != _flat(right) and re.search(r"(?:^|\s)" + re.escape(word) + r"(?:\s|$)", n) for n in _installed()):
            return text          # "close cloud" with Creative Cloud installed: maybe that one, so leave it
        return m.group("head") + right + m.group("tail")
    return text


def _tense(text: str) -> str:
    m = _PAST.match(text)
    if not m or "?" in text or not exact_app(m.group("obj")):
        return text              # "Closed the deal.", "Closed calculator already.": talk
    return m.group("lead") + _TENSE[m.group("verb").casefold()] + " " + m.group("obj") + m.group("tail")


def normalise(text: str) -> str:
    """The sentence as he meant it, for routing. The channel tag in front ("[Spoken into the PC]") is kept as it is."""
    if not isinstance(text, str) or not text.strip() or len(text) > 400:
        return text
    tag = _CHANNEL.match(text)
    head, body = (tag.group(0), text[tag.end():]) if tag else ("", text)
    stripped = body.strip()
    if not stripped:
        return text
    fixed = _once(_name(stripped))      # his name needs no catalogue
    try:
        fixed = _tense(_app(fixed))
    except Exception:  # noqa: BLE001 - no catalogue: fail closed, nothing that depends on it is rewritten
        pass
    return text if fixed == stripped else head + fixed
