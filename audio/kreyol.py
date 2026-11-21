"""Haitian Creole text for the voice: numbers, clock times, dates and a few signs as Creole words.

Meta's MMS Creole voice (audio/tts) reads letters only: its alphabet is a-z, è, ò, à, the apostrophe and the hyphen.
A digit, a "%" or an "é" is silently dropped, so "3 minit" came out as "minit". Everything here is plain rules,
no model: prepare() is what the voice is given; number(), clock() and date() are also used for the quick answers.
"""
import os
import re
from pathlib import Path

# The Creole voice trained on the owner's own reading (scripts/kreyol_finetune.py writes it; models/ is never
# committed). JARVIS_KREYOL_TUNED=off goes back to the stock voice; a path there names another folder.
TUNED_DIR = Path(__file__).resolve().parent.parent / "models" / "kreyol-tuned"
TUNED_FILES = ("config.json", "model.safetensors", "vocab.json")


def tuned_dir() -> Path:
    named = os.getenv("JARVIS_KREYOL_TUNED", "").strip()
    return Path(named) if named and named.lower() not in ("off", "on", "0", "1", "stock") else TUNED_DIR


def tuned_model() -> Path | None:
    """The folder of the tuned Creole voice when it is complete and not switched off, else None (the stock voice)."""
    if os.getenv("JARVIS_KREYOL_TUNED", "").strip().lower() in ("off", "0", "stock"):
        return None
    folder = tuned_dir()
    return folder if all((folder / name).is_file() for name in TUNED_FILES) else None


_ONES = ["zewo", "en", "de", "twa", "kat", "senk", "sis", "sèt", "uit", "nèf", "dis", "onz", "douz", "trèz", "katòz",
         "kenz", "sèz", "disèt", "dizuit", "diznèf"]
_TENS = {20: "ven", 30: "trant", 40: "karant", 50: "senkant", 60: "swasant", 80: "katreven"}
# How a tens word joins the unit after it: "venteyen", "vennde", "ventuit", "ventnèf"; "tranteyen", "trannde".
_JOIN = {20: ("vent", "venn"), 30: ("trant", "trann"), 40: ("karant", "karann"), 50: ("senkant", "senkann"),
         60: ("swasant", "swasann")}
_HOURS = {1: "inè", 2: "dezè", 3: "twazè", 4: "katrè", 5: "senkè", 6: "sizè", 7: "setè", 8: "uitè", 9: "nevè",
          10: "dizè", 11: "onzè", 12: "douzè"}
DAYS = ["lendi", "madi", "mèkredi", "jedi", "vandredi", "samdi", "dimanch"]              # Monday first
MONTHS = ["janvye", "fevriye", "mas", "avril", "me", "jen", "jiyè", "out", "septanm", "oktòb", "novanm", "desanm"]


def number(n: int) -> str:
    """0..999,999 in Creole words ("venteyen", "swasanndis", "katreven en", "de san senk", "twa mil")."""
    n = int(n)
    if n < 0:
        return "mwens " + number(-n)
    if n < 20:
        return _ONES[n]
    if n < 70:
        tens, unit = n // 10 * 10, n % 10
        if not unit:
            return _TENS[tens]
        with_t, with_n = _JOIN[tens]
        if unit == 1:
            return with_t + "eyen"
        return (with_t if unit in (8, 9) else with_n) + _ONES[unit]
    if n < 80:                                   # 70-79: sixty and ten..nineteen
        return {70: "swasanndis", 71: "swasantonz"}.get(n, "swasann" + _ONES[n - 60])
    if n < 100:                                  # 80-99: four twenties (and ten..nineteen)
        return "katreven" if n == 80 else "katreven " + _ONES[n - 80] if n < 90 else "katreven" + _ONES[n - 80]
    if n < 1000:
        head = "san" if n < 200 else _ONES[n // 100] + " san"
        return head if n % 100 == 0 else head + " " + number(n % 100)
    if n < 1_000_000:
        head = "mil" if n < 2000 else number(n // 1000) + " mil"
        return head if n % 1000 == 0 else head + " " + number(n % 1000)
    return " ".join(_ONES[int(d)] for d in str(n))


def clock(hour: int, minute: int) -> str:
    """The time of day in words: "twazè ven nan apremidi", "midi", "minwi senk"."""
    part = "nan maten" if hour < 12 else "nan apremidi" if hour < 18 else "nan aswè"
    if hour % 12 == 0:
        head, part = ("minwi" if hour == 0 else "midi"), ""
    else:
        head = _HOURS[hour % 12]
    mins = "" if not minute else "edmi" if minute == 30 else "eka" if minute == 15 else number(minute)
    return " ".join(p for p in (head, mins, part) if p)


def date(day) -> str:
    """A datetime/date as "jedi douz novanm"."""
    return f"{DAYS[day.weekday()]} {'premye' if day.day == 1 else number(day.day)} {MONTHS[day.month - 1]}"


_TIME = re.compile(r"\b([01]?\d|2[0-3])(?::|\s?[hè]\s?)([0-5]\d)\b")
_SIGNS = (("%", " pousan "), ("&", " ak "), ("$", " dola "), ("°", " degre "), ("+", " plis "), ("=", " egal "),
          ("@", " nan "))
_ABBREVIATIONS = (("elatriye", re.compile(r"\betc\b\.?", re.I)), ("mesye", re.compile(r"\b(?:m|mr)\.(?=\s)", re.I)),
                  ("madanm", re.compile(r"\b(?:mme|mrs)\.?(?=\s)", re.I)), ("doktè", re.compile(r"\bdr\.?(?=\s)", re.I)),
                  ("kilomèt", re.compile(r"(?<=\d)\s?km\b", re.I)), ("minit", re.compile(r"(?<=\d)\s?min\b", re.I)))
# Letters the voice has no sound for, as the Creole letters that say the same.
_LETTERS = str.maketrans({"é": "e", "ê": "è", "ë": "e", "â": "a", "á": "a", "î": "i", "ï": "i", "í": "i", "ô": "o",
                          "ó": "o", "û": "ou", "ù": "ou", "ú": "ou", "ü": "u", "ç": "s", "ñ": "ny", "œ": "e",
                          "q": "k", "x": "ks", "’": "'", "–": "-"})


def prepare(text: str) -> str:
    """What the Creole voice is given for `text`: lower case, numbers and times in words, signs and a few abbreviations
    spoken, letters outside its alphabet mapped, everything else (markdown, punctuation) dropped."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")            # a markdown link: its words
    t = re.sub(r"https?://\S+", " ", t)
    for word, pattern in _ABBREVIATIONS:
        t = pattern.sub(" " + word, t)
    t = _TIME.sub(lambda m: " " + clock(int(m.group(1)), int(m.group(2))).split(" nan ")[0] + " ", t)
    t = re.sub(r"\$\s?(\d+(?:[.,]\d+)?)", r"\1 dola", t)               # "$12" is said "douz dola"
    for sign, word in _SIGNS:
        t = t.replace(sign, word)
    t = re.sub(r"(?<=\d)[,  ](?=\d{3}\b)", "", t)            # 1,500 -> 1500
    t = re.sub(r"(\d+)\.(\d+)", lambda m: f"{number(int(m.group(1)))} pwen " +
               " ".join(_ONES[int(d)] for d in m.group(2)), t)
    t = re.sub(r"\d+", lambda m: " " + number(int(m.group(0))) + " ", t)
    t = t.lower().translate(_LETTERS)
    t = re.sub(r"[^a-zèòà' -]+", " ", t)
    return " ".join(t.split())


def pieces(text: str) -> list[tuple[str, float]]:
    """The text as what to say and the pause after it: [(prepared words, seconds of silence)]. The voice drops
    punctuation, so a full stop or a comma would otherwise leave no breath at all."""
    out = []
    for part in re.split(r"(?<=[.!?;:,…])\s+|\n+", text or ""):
        part = part.strip()
        mark = part[-1:] if part[-1:] in ".!?;:,…" else ""
        said = prepare(part)
        if not said:
            continue
        pause = 0.0 if not mark else 0.12 if mark[0] == "," else 0.28
        if out and len(said.split()) < 2 and out[-1][1] <= 0.12:
            out[-1] = (out[-1][0] + " " + said, pause)                 # a one-word clause joins the one before it
        else:
            out.append((said, pause))
    return out
