"""Tell English, Haitian Creole and French apart, sentence by sentence, so each
sentence goes to the right voice and Jarvis knows which language you spoke."""
import re

# Common Kreyòl words that are rare in English and French.
_CREOLE = set("""
mwen m ou w li l nou n yo y se pa ki sa gen ap pral te ka nan avèk avek ak pou kounye kounya
bonjou bonswa mèsi mesi wi kijan kòman koman kisa kote poukisa anpil bagay konnen vle fè fe
bezwen kapab kab tankou sou lè isit yon twa kat mezanmi chè frè sè tout bèl bel rad mache
manje travay kay jodi demen maten aswè oke dakò enben alò alo paske pito ankò anko deja janm
toujou mesye madanm pitit zanmi lakay byen mal gade tande pale di bay pran kite vini ale
tèt kò soulye pantalon chemiz mayo koulè nwa blan wouj ble jòn vèt gri tou piti gwo bon move
""".split())
# French function words that Kreyòl doesn't use.
_FRENCH = set("""
je j' tu il elle nous vous ils elles le les des du au aux une est sont suis êtes sommes c'est
ce cette ces qu' que qui quoi pourquoi comment où avec dans mais très être avoir ai as avons
avez ont fait faire mon ton son ma ta sa mes tes ses votre vos notre nos leur leurs bonjour
merci oui non aujourd'hui demain hier peux peut veux veut voudrais pouvez voulez comme aussi
encore alors donc parce pas ne n' d' l' s' m' t' y en sur pour par plus moins bien bon
bonsoir rien salut monsieur madame
""".split())
_ENGLISH = set("""
the a an and or but of to in on at for with is are was were be been it this that these those
i you he she we they me him her us them my your his our their what which who how why when
where do does did can could would should will have has had not no yes please thanks thank
sir okay ok just about from up down out so if then than there here now today tomorrow
i'm i'll i've i'd it's that's there's here's what's let's don't doesn't didn't can't won't isn't
aren't wasn't couldn't wouldn't you're you'll you've we're we'll they're he's she's
by into over again also only even still all any some more most much many every each other
nothing something everything anything one two first last next new good great right well back
way get got go going come look looks see make know think need want like time day night
morning evening week year try done because while after before around off very really ready
help find found keep turn open close start stop running either goodnight
""".split())
# Letters and marks English never uses (French/Kreyol accents are scored above).
_FOREIGN = re.compile(r"[¿¡ñáíóúãõäöüßåøł]", re.I)


# Set while a lesson or interpreter session involves another language: short
# sentences that aren't clearly English/French/Kreyol are then that language.
lesson_hint: str | None = None


# ---- a language lesson's own phrases ------------------------------------------------------------------------------
# The language teacher marks every target-language phrase it is about to say (tools/langlesson): the speaker then
# uses that language's voice for exactly those words, slowly when asked, and Jarvis's own voice for the English
# around them. A phrase marked with "" is for the eye only (pinyin or romaji in brackets) and is not spoken.
# The marks reach only the lines the lesson itself wrote (mark_line), or a phrase asked for alone (the Languages
# page's Listen), and inside those only whole words: a course word such as "de", "un" or "six" must never cut an
# ordinary reply ("Opening", "decision", "understand", "six new emails") into another voice.
_marks: dict[str, tuple[str, bool]] = {}
_lines: dict[str, None] = {}
MAX_MARKS = 300
MAX_LINES = 60
_EDGE = "0-9A-Za-z\u00C0-\u024F\u1E00-\u1EFF"            # Latin letters and digits; kana and characters need no space


def mark(phrase: str, code: str, slow: bool = False) -> None:
    phrase = " ".join(str(phrase or "").split())
    if not phrase:
        return
    _marks.pop(phrase, None)
    _marks[phrase] = (code, slow)
    while len(_marks) > MAX_MARKS:
        _marks.pop(next(iter(_marks)))


def mark_line(text: str) -> None:
    """A line the lesson is about to say: its sentences are the only ones the marks may cut."""
    line = " ".join(str(text or "").split())
    if not line:
        return
    _lines.pop(line, None)
    _lines[line] = None
    while len(_lines) > MAX_LINES:
        _lines.pop(next(iter(_lines)))


def clear_marks() -> None:
    _marks.clear()
    _lines.clear()


def _whole(phrase: str) -> str:
    before = f"(?<![{_EDGE}])" if re.match(f"[{_EDGE}]", phrase) else ""
    after = f"(?![{_EDGE}])" if re.search(f"[{_EDGE}]$", phrase) else ""
    return before + re.escape(phrase) + after


def segments(text: str) -> list[tuple[str | None, str, bool]] | None:
    """A line cut at the marked phrases: [(code or None for ordinary speech, piece, slow)], silent pieces left out.
    None when nothing in it is marked (the caller then speaks it as it always did)."""
    if not _marks or not text:
        return None
    said = " ".join(text.split())
    if said.strip(" .!?\u3002\uFF1F\uFF01\"'") not in _marks and not any(said in line for line in _lines):
        return None                                        # not the lesson's line: when unsure, speak it as always
    found = [p for p in sorted(_marks, key=len, reverse=True) if p in text]
    hits = list(re.finditer("|".join(_whole(p) for p in found), text)) if found else []
    if not hits:
        return None
    out, at = [], 0
    for m in hits:
        before = text[at:m.start()]
        if re.search(r"\w", before):
            out.append((None, before.strip().lstrip(".,;:!?) ").strip(), False))
        code, slow = _marks[m.group(0)]
        if code:
            out.append((code, m.group(0), slow))
        at = m.end()
    if re.search(r"\w", text[at:]):
        out.append((None, text[at:].strip().lstrip(".,;:!?) ").strip(), False))
    return out


def script(text: str) -> str | None:
    """'ja' for text with kana, 'zh' for Chinese characters without kana, else None. (The word lists below only know
    Latin letters: Japanese and Chinese used to come out as English and were read by the English voice.)"""
    if re.search(r"[぀-ヿ]", text or ""):
        return "ja"
    return "zh" if re.search(r"[一-鿿]", text or "") else None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zàâçéèêëîïôûùüÿœæ']+", text.lower())


def scores(text: str) -> dict[str, float]:
    text = (text or "").replace("’", "'").replace("‘", "'")   # a typographic apostrophe is an apostrophe: "I’m fine, mesye." tied en/ht (live 2026-11-15)
    words = _words(text)
    if not words:
        return {"en": 0.0, "fr": 0.0, "ht": 0.0}
    n = len(words)
    split = [p for w in words for p in re.split(r"(?<=')", w) if p]  # "c'est" -> "c'", "est"
    fr = sum(w in _FRENCH for w in split) / n
    ht = sum(w.strip("'") in _CREOLE for w in words) / n
    en = sum(w in _ENGLISH for w in words) / n
    if any(c in text.lower() for c in "èò") and not any(c in text.lower() for c in "éêâçœ"):
        ht += 0.15  # è/ò without é/ê/â/ç is very Kreyòl
    if any(c in text.lower() for c in "éêâçœ"):
        fr += 0.15
    return {"en": en, "fr": fr, "ht": ht}


def detect(text: str) -> str:
    """'en', 'fr', 'ht', or 'other' (some other language, e.g. Spanish in a lesson)."""
    if script(text):
        return "other"
    s = scores(text)
    priority = {"ht": 2, "fr": 1, "en": 0}  # ties go to Kreyol/French: English rarely ties with them
    best = max(s, key=lambda k: (round(s[k], 3), priority[k]))
    if s[best] < 0.15:
        # Unsure. Outside a lesson that's English with few common words ("I'll try again.", "Nothing looks wrong."),
        # not a foreign language: those went to the online multilingual voice, a different voice mid-reply.
        words = _words(text)
        return "other" if words and (lesson_hint or (len(words) >= 3 and _FOREIGN.search(text))) else "en"
    if best == "fr" and s["ht"] >= s["fr"] * 0.9:
        return "ht"  # shared words; lean Kreyòl when it's close
    return best


def is_creole(text: str) -> bool:
    return detect(text) == "ht"


# ---- which language is this conversation in? ----------------------------------------------------------------------
# He switches language by speaking it: no command. Each line he says or types is identified (identify, or the
# recogniser's own evidence when it was spoken: heard), and a conversation keeps ONE language (Sticky) that changes
# only on confident evidence: one clear sentence, or two lines in a row pointing the same way. One unclear short line
# ("Wi.", "OK", an app's name) never flips it. The reply language, the voice and the next turn's recognition follow it.
USED = ("en", "ht", "fr", "es")            # the languages the household really speaks to him
LESSON_ONLY = ("zh", "ja")                 # heard and answered only while a lesson teaches them
NAMES = {"en": "English", "ht": "Haitian Creole", "fr": "French", "es": "Spanish", "zh": "Mandarin Chinese",
         "ja": "Japanese"}
CLEAR = 0.8                                # evidence at or above this flips the conversation at once
SOME = 0.4                                 # below this a line says nothing about the language

_CREOLE_MORE = set("""
ye sak pase kap boule tanpri souple eske èske konbyen mete minit segonn èdtan revèy ouvri fèmen limyè mizik jwe rele
ansanm deyò lapli cho frèt grangou swaf dòmi leve pwoblèm menm bondye bonnwit orevwa babay eskize padon kontan tris
fatige malad lekòl legliz machin telefòn òdinatè dlo diri pwa poul vyann pen kafe kilè kiyès kisa poukisa bagay
jodia kounyea mwen map wap lap nap yap pral konprann renmen panse kwè chache jwenn achte vann ban montre ede eseye
kòmanse fini tounen rete viv chita kanpe mache kouri ekri li tande gade santi swa oswa men si paske lè depi jiska
anvan apre pandan toutan tanpri wi non dakò anmwe mezi anyen okenn chak kèk lòt menm sèlman trè twò ase plis mwens
""".split())
_SPANISH = set("""
el la los las un una unos unas y o pero de del al en con por para que qué como cómo cuando cuándo donde dónde quien
quién es son está están estoy estás soy eres somos tengo tienes tiene tenemos quiero quieres quiere puedo puedes
puede hola gracias adiós buenos buenas días tardes noches sí no favor mi tu su mis tus sus yo tú él ella nosotros
ustedes ellos me te se lo le nos muy más menos bien mal hoy mañana ayer ahora aquí allí esto eso este esta hay hace
hacer voy vas va vamos necesito hora tiempo señor también porque nada algo todo mucho poco usted cuál cuánto cuántos
dime dame pon abre cierra quiero gusta hablar hablo hablas español entiendo sabes sé tal vez nunca siempre ya todavía
""".split())
_FRENCH_MORE = set("""
quelle quel heure est-il suis-je ça cela ici là maintenant toujours jamais beaucoup peu trop assez tout tous toute
voilà s'il plaît parle parler parlez français comprends sais savoir dis dire mets ouvre ferme peux-tu veux-tu
quand combien quelqu'un quelque chose temps jour nuit matin soir maison travail besoin envie
""".split())
_ENGLISH_MORE = set("""
play music show tell give put set call send check read write remind add remove delete cancel pause resume volume
hello hi hey thanks bye what's who's how's am doing fine weather timer minutes minute hours hour seconds
light lights computer phone message email calendar game screen window app tab file folder search
""".split())
_SETS = {"en": _ENGLISH | _ENGLISH_MORE, "ht": _CREOLE | _CREOLE_MORE, "fr": _FRENCH | _FRENCH_MORE, "es": _SPANISH}
_TOKEN = re.compile(r"[a-zàâçéèêëîïôûùüÿœæñáíóúò][a-zàâçéèêëîïôûùüÿœæñáíóúò'-]*")
_SHARED = 0.34                             # a word several of the languages use counts this much for each


def _in(word: str, known: set) -> bool:
    return word in known or word.strip("'-") in known or any(p in known for p in re.split(r"(?<=')|-", word) if p)


def identify(text: str) -> tuple[str | None, float]:
    """(language code or None, how sure 0..1) for one line. A whole sentence in one of his languages is CLEAR (two
    words only that language has, and a lead over the others); one telling word is SOME evidence; none is (None, 0).
    Japanese and Chinese are told by their script."""
    text = text or ""
    cjk = script(text)
    if cjk:
        return cjk, 1.0
    words = _TOKEN.findall(text.lower())
    if not words:
        return None, 0.0
    score = dict.fromkeys(USED, 0.0)
    own = dict.fromkeys(USED, 0)
    for word in words:
        langs = [code for code in USED if _in(word, _SETS[code])]
        if len(langs) == 1:
            own[langs[0]] += 1
            score[langs[0]] += 1
        for code in langs if len(langs) > 1 else ():
            score[code] += _SHARED
    low = text.lower()
    if re.search(r"[¿¡ñ]", low):
        score["es"] += 1
        own["es"] += 1
    if re.search(r"[èò]", low) and not re.search(r"[éêâçœ]", low):
        score["ht"] += 0.5                 # è/ò without é/ê/â/ç is very Kreyòl
    if re.search(r"[êâçœ]", low):
        score["fr"] += 0.5
    ranked = sorted(USED, key=lambda code: (score[code], own[code]), reverse=True)
    best, second = ranked[0], ranked[1]
    if score[best] <= 0:
        return None, 0.0
    lead = (score[best] - score[second]) / len(words)
    if own[best] >= 2 and lead >= 0.15:
        return best, 0.9
    if own[best] >= 1 and score[best] > score[second]:
        return best, 0.5
    return best, 0.3


class Sticky:
    """One conversation's language. observe() takes a line's (code, confidence) and returns the language now."""

    def __init__(self, current: str = "en"):
        self.current = current
        self.pending: str | None = None    # an unclear line pointed here; a second one flips
        self.left_english = False          # it has been in another language: "back to English" is then worth a note

    def observe(self, code: str | None, confidence: float) -> str:
        if code is None or confidence < SOME:
            return self.current            # says nothing: neither flips nor forgets a pending one
        if code not in USED and not (code in LESSON_ONLY and lesson_hint == code):
            return self.current            # a language he does not use with Jarvis (a recogniser's wild guess)
        if code == self.current:
            self.pending = None
            return self.current
        if confidence >= CLEAR or self.pending == code:
            self.current, self.pending = code, None
            self.left_english = self.left_english or code != "en"
        else:
            self.pending = code
        return self.current


_conversations: dict[str, Sticky] = {}
_heard: tuple[str, str, float] | None = None     # the recogniser's evidence for the line it just wrote
_spoken_key: str | None = None                   # the conversation the microphone is in
speaking: str | None = None                      # the language of the reply being spoken (an unsure sentence's voice)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def conversation(key: str = "owner") -> Sticky:
    return _conversations.setdefault(key, Sticky())


def reset() -> None:
    global _heard, _spoken_key, speaking
    _conversations.clear()
    _heard, _spoken_key, speaking = None, None, None


def heard(text: str, code: str, confidence: float) -> None:
    """The recogniser's own evidence for a line it transcribed (audio/stt): used when that same line reaches observe."""
    global _heard
    _heard = (_norm(text), code, float(confidence)) if text and code else None


def observe(key: str, text: str) -> str:
    """A line he said or typed in conversation `key`: the conversation's language after it."""
    global _heard, _spoken_key
    state = conversation(key)
    code, confidence = identify(text)
    evidence, _heard = _heard, None
    if evidence and evidence[0] and evidence[0] in _norm(text):
        _spoken_key = key
        _, by_ear, sure = evidence
        if code == by_ear or code is None or confidence < SOME:
            code, confidence = by_ear, max(sure, confidence if code == by_ear else 0.0)
        elif confidence < CLEAR:
            code, confidence = by_ear, min(sure, 0.5)      # ear and words disagree, neither clearly: unclear
        # else: the words are a clear sentence in another language than the ear's guess: the words decide
    return state.observe(code, confidence)


def current(key: str = "owner") -> str:
    return conversation(key).current


def spoken_language() -> str | None:
    """The language of the conversation at the microphone (None before any spoken turn): the recogniser leans to it."""
    return _conversations[_spoken_key].current if _spoken_key in _conversations else None


def reply_note(key: str = "owner", said: str = "", uid: str | None = None) -> str:
    """The per-request note that tells the model which language to answer in. '' for a conversation that has only
    ever been English (an English turn is then byte for byte what it was). said: his line, so a Creole note carries
    the bank sentences nearest to it (core.kreyol_bank; uid's own corrections first)."""
    state = conversation(key)
    if state.current == "en":
        if not state.left_english:
            return ""
        return ("(Language: they are speaking English again. Answer in English only, with no Creole, French or "
                "Spanish words.)")
    name = NAMES.get(state.current, state.current)
    note = (f"(Language: they are speaking {name}. Answer in {name} only, in the same short natural spoken style, "
            f"unless their newest message is clearly a whole sentence in another language.")
    if state.current == "ht":
        note += " " + KREYOL_GUIDE
        try:
            from core import kreyol_bank
            note += " " + kreyol_bank.grounding(said, uid)
        except Exception:  # noqa: BLE001  (the guide alone still stands)
            pass
    return note + ")"


# Few-shot grounding for the local model's Creole (it drifts into French spelling and French words). Standard
# orthography, everyday sentences; the pairs are in the built-in course's style (core/lang_curriculum).
KREYOL_GUIDE = (
    "Write standard Haitian Creole (Kreyòl ayisyen), never French: no silent letters, no 'je/vous/est/c'est/qu', k "
    "and not c or qu (kisa, kote, kijan), w and y (mwen, ou, li, nou, yo), ap for now, te for the past, pral for the "
    "future, pa for not; the article comes after the noun (kay la, machin nan). Examples: 'How are you?' = 'Kijan ou "
    "ye?'; 'I am fine, thank you.' = 'Mwen byen, mèsi.'; 'What do you need?' = 'Kisa ou bezwen?'; 'I do not know.' "
    "= 'Mwen pa konnen.'; 'I am working now.' = 'M ap travay kounye a.'; 'I will do it tomorrow.' = 'Mwen pral fè "
    "li demen.'; 'It is done.' = 'Li fèt.'; 'I can help you.' = 'Mwen ka ede ou.'; 'There is no problem.' = 'Pa gen "
    "pwoblèm.'; 'What time is it?' = 'Ki lè li ye?'.")


def voice_language(text: str) -> str:
    """Which voice says this sentence of his: 'en', 'ht', 'fr', 'es' or 'other'. As detect(), plus Spanish, and a
    sentence too short to tell goes to the language the reply is in (speaking)."""
    code, confidence = identify(text)
    if code in ("ja", "zh"):
        return "other"
    if code in USED and confidence >= CLEAR:
        return code                        # a whole sentence: its own words decide ("Sak pase?", "Je veux tout savoir")
    base = detect(text)
    if speaking in ("ht", "fr", "es") and base != speaking and confidence < CLEAR and code in (None, speaking):
        return speaking
    return base
