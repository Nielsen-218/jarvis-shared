"""Language controls: lock the conversation language, and interpreter mode
(translate out loud between the user and someone else)."""
import re
import unicodedata

from tools.registry import tool

# Whisper language codes for languages people commonly ask about, by their English names and the names the
# household uses in its own languages ("je vais parler français", "m ap pale kreyòl ayisyen").
CODES = {
    "english": "en", "anglais": "en", "angle": "en",
    "french": "fr", "français": "fr", "francais": "fr", "franse": "fr",
    "haitian creole": "ht", "haitian": "ht", "creole": "ht", "kreyol": "ht", "kreyòl": "ht", "kreyol ayisyen": "ht",
    "kreyòl ayisyen": "ht", "ayisyen": "ht", "créole": "ht", "créole haïtien": "ht",
    "spanish": "es", "español": "es", "espanol": "es", "castellano": "es", "panyòl": "es", "panyol": "es",
    "portuguese": "pt", "português": "pt", "portugues": "pt", "italian": "it", "italiano": "it",
    "german": "de", "deutsch": "de", "dutch": "nl", "nederlands": "nl", "japanese": "ja",
    "chinese": "zh", "mandarin": "zh", "korean": "ko", "arabic": "ar", "russian": "ru", "hindi": "hi",
    "swahili": "sw", "yoruba": "yo", "tagalog": "tl", "vietnamese": "vi", "turkish": "tr", "polish": "pl",
}


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def named_in(text: str) -> set[str]:
    """The codes of every language named in the text, by any of its names, accents and case aside
    ("only speak Kreyol with me" -> {'ht'}; "Please make corrections too." -> set())."""
    plain = _plain(text or "")
    return {code for name, code in CODES.items()
            if re.search(rf"(?<![a-z]){re.escape(_plain(name))}(?![a-z])", plain)}

# Native voices for lessons and interpreting (edge-tts); anything else uses a multilingual voice.
NATIVE_VOICES = {
    "es": "es-MX-JorgeNeural", "pt": "pt-BR-AntonioNeural", "it": "it-IT-DiegoNeural",
    "de": "de-DE-ConradNeural", "nl": "nl-NL-MaartenNeural", "ja": "ja-JP-KeitaNeural",
    "zh": "zh-CN-YunxiNeural", "ko": "ko-KR-InJoonNeural", "ar": "ar-SA-HamedNeural",
    "ru": "ru-RU-DmitryNeural", "hi": "hi-IN-MadhurNeural", "sw": "sw-KE-RafikiNeural",
    "tl": "fil-PH-AngeloNeural", "vi": "vi-VN-NamMinhNeural",
    "tr": "tr-TR-AhmetNeural", "pl": "pl-PL-MarekNeural",
}
DEFAULT_OTHER_VOICE = "en-US-AndrewMultilingualNeural"

transcriber = None  # set by main.py in voice mode
speaker = None  # set by main.py
interpreter: dict | None = None  # {"a": "en", "b": "ht", "names": ("English", "Haitian Creole")}


def set_foreign_language(code: str | None) -> None:
    """A lesson/interpreter language other than English, French or Kreyol: make
    speech recognition listen for it and speak it with a native voice."""
    from audio import language

    foreign = code if code not in (None, "en", "fr", "ht") else None
    language.lesson_hint = foreign
    if transcriber is not None:
        transcriber.extra_language = foreign
    if speaker is not None:
        speaker.other_voice = NATIVE_VOICES.get(foreign, DEFAULT_OTHER_VOICE)


def code_for(language: str) -> str | None:
    lang = language.strip().lower()
    return CODES.get(lang) or (lang if len(lang) == 2 else None)


@tool(
    "Lock which language the user's speech is recognized as ('only speak Kreyol with me', 'je vais "
    "parler français'), or go back to automatic detection. Locking helps when you keep mishearing.",
    {"language": {"type": "string", "description": "Language name like 'Haitian Creole', 'French', 'English', or 'auto'"}},
)
def set_conversation_language(language: str) -> str:
    if transcriber is None:
        return "Speech recognition isn't running (text mode); nothing to change."
    if language.strip().lower() == "auto":
        transcriber.forced_language = None
        return "Back to automatic language detection."
    code = code_for(language)
    if code is None:
        return f"Unknown language '{language}'."
    transcriber.forced_language = code
    return f"Speech recognition locked to {language}."


@tool(
    "Start interpreter mode: from now on, everything said in one language is translated out loud "
    "into the other (e.g. the user speaks English, their grandmother speaks Kreyol). Jarvis stays "
    "listening until someone says 'stop translating'. Confirm the two languages first.",
    {
        "language_a": {"type": "string", "description": "e.g. 'English'"},
        "language_b": {"type": "string", "description": "e.g. 'Haitian Creole'"},
    },
)
def start_interpreter(language_a: str, language_b: str) -> str:
    global interpreter
    a, b = code_for(language_a), code_for(language_b)
    if not a or not b:
        return f"I don't know the language code for '{language_a if not a else language_b}'."
    interpreter = {"a": a, "b": b, "names": (language_a, language_b)}
    set_foreign_language(next((c for c in (a, b) if c not in ("en", "fr", "ht")), None))
    return (f"Interpreter mode on: {language_a} <-> {language_b}. Tell the user to just talk, and say "
            "'stop translating' when they're done.")


def stop_interpreter() -> None:
    global interpreter
    interpreter = None
    set_foreign_language(None)
