"""The heads-up before something that cannot be undone, in French and Kreyol (core/riskwarn.py is English only).

The same rules as the English one: ONE short warning that says what it does and that the call is his; his "do it anyway" gets an honest answer that says
nothing ran and where he can do it himself; nothing here runs a command. Only the two common drastic orders (erase or format a drive or the whole
machine, delete everything) are recognised, conservatively: a question, a past tense, a negation or a named folder is not one. When unsure it says
nothing: the approvals layer (core.approvals) still asks before any real deletion.
"""
from __future__ import annotations

import re

MARK = {"fr": "« fais-le quand même »", "ht": "« fè l kanmenm »"}
_CALL = {"fr": "c'est votre décision", "ht": "se ou menm ki deside"}
_ACK_HEAD = {"fr": "Compris, ", "ht": "Konprann, "}

_FILLER = re.compile(r"^\W*(?:(?:allez|alors|bon|bien|ok|okay|jarvis|s'il (?:te|vous) pla[iî]t|svp|ou|wi|non|ebyen|bon|tanpri|jarvis)[,\s]+)+", re.I)
_QUESTION = {"fr": re.compile(r"^\W*(?:comment|pourquoi|quand|où|quel|quelle|quels|est-ce|est ce|peux-tu|peut-on|faut-il|que|qu'est)\b", re.I),
             "ht": re.compile(r"^\W*(?:kijan|poukisa|kilè|ki|eske|èske|kote|kisa|ka m|èske m)\b", re.I)}

_TAIL = r"(?:\s+(?:s'il vous pla[iî]t|svp|maintenant|tout de suite|définitivement|complètement|tanpri|kounye a|pou tout tan|nèt))*\W*$"
_FR_VERB = r"(?:re-?formate[rz]?|formate[rz]?|efface[rz]?|supprime[rz]?|vide[rz]?|nettoie[rz]?|détruis|détruire)"
_FR_DRIVE = (r"(?:(?:tout\s+)?(?:le |mon |ce |cet |tout le )?(?:disque(?: dur)?|ssd|lecteur)(?:\s+(?P<l>[c-z]):?)?|"
             r"(?:le |mon )?(?:disque|lecteur)\s+(?P<l2>[c-z])\b|(?P<l3>[c-z]):\\?|(?:tout\s+)?(?:le |mon |cet )?(?:pc|ordinateur|ordi|machine))")
_FR_ALL = (r"(?:(?:tout|tous mes (?:fichiers|documents|photos|données)|toutes mes (?:photos|données))"
           r"(?:\s+(?:sur|de|dans)\s+(?:mon|le|cet)\s+(?:pc|ordinateur|ordi|disque(?: dur)?|machine))?)")
_HT_VERB = r"(?:fòmate|fòma|efase|siprime|retire|vide|netwaye|detwi)"
_HT_DRIVE = (r"(?:(?:tout\s+)?(?:disk|lekti[yi]e?)(?:\s+(?P<l>[c-z]):?)?(?:\s+(?:la|a|mwen))?|(?:disk|lekti[yi]e?)\s+(?P<l2>[c-z])\b|(?P<l3>[c-z]):\\?|"
             r"(?:tout\s+)?(?:òdinatè|odinate|pc|machin)(?:\s+(?:mwen|la|sa|a))?)")
_HT_ALL = (r"(?:tout\s+(?:bagay|fichye|dosye|foto|dokiman|done)(?:\s+mwen)?(?:\s+yo)?"
           r"(?:\s+sou\s+(?:òdinatè|odinate|pc|disk|machin)(?:\s+(?:mwen|la|a))?)?)")
_KINDS = {
    "fr": (("drive", re.compile(rf"^{_FR_VERB}\s+{_FR_DRIVE}{_TAIL}", re.I)),
           ("everything", re.compile(rf"^{_FR_VERB}\s+{_FR_ALL}{_TAIL}", re.I))),
    "ht": (("drive", re.compile(rf"^{_HT_VERB}\s+{_HT_DRIVE}{_TAIL}", re.I)),
           ("everything", re.compile(rf"^{_HT_VERB}\s+{_HT_ALL}{_TAIL}", re.I))),
}
_PLACED = {"fr": re.compile(r"\b(?:sur|de|dans)\s+(?:mon|le|cet)\s+(?:pc|ordinateur|ordi|disque|machine)\b|\btous mes (?:fichiers|documents|photos|données)\b|\btoutes mes (?:photos|données)\b", re.I),
           "ht": re.compile(r"\bsou\s+(?:òdinatè|odinate|pc|disk|machin)\b|\btout (?:fichye|dosye|foto|dokiman|done)\b", re.I)}
_INSIST = {
    "fr": re.compile(r"^\W*(?:(?:oui|ouais|non|ok|bon|alors|jarvis|monsieur)[,\s]+)*(?:fais[- ]le(?: quand même)?|faites[- ]le(?: quand même)?|vas-y|allez-y|"
                     r"je suis (?:sûr|sûre|certain|certaine)|je sais ce que je fais|c'est mon (?:pc|ordinateur|choix|disque)|continue|continuez|procède|procédez)"
                     r"(?:[,\s]+(?:quand même|s'il vous pla[iî]t|svp|jarvis|monsieur))*\W*$", re.I),
    "ht": re.compile(r"^\W*(?:(?:wi|non|ok|bon|ebyen|jarvis|mesye)[,\s]+)*(?:fè l(?: kanmenm| non)?|fè sa(?: kanmenm| non)?|ale non|kontinye|mwen sèten|mwen sere|"
                     r"mwen konnen sa m ap fè|se (?:pc|òdinatè|chwa|machin) mwen)(?:[,\s]+(?:kanmenm|tanpri|jarvis|mesye))*\W*$", re.I)}


def _clean(text: str) -> str:
    try:
        from core import chatturn
        text = chatturn._plain(text)
    except Exception:  # noqa: BLE001
        text = str(text or "").strip()
    text = " ".join(text.replace("’", "'").split())
    return _FILLER.sub("", text).strip()


def kind(text: str, lang: str) -> tuple:
    """('drive' | 'everything', 'D' or '') for a French or Kreyol order that is drastic, else (None, '')."""
    if lang not in _KINDS:
        return (None, "")
    line = _clean(text)
    if not line or len(line.split()) > 20 or line.endswith("?") or _QUESTION[lang].match(line):
        return (None, "")
    for name, rx in _KINDS[lang]:
        found = rx.match(line)
        if found:
            if name == "everything" and not _PLACED[lang].search(line):
                return (None, "")
            letter = ""
            if name == "drive":
                g = found.groupdict()
                letter = (g.get("l") or g.get("l2") or g.get("l3") or "").upper()
            return (name, letter)
    return (None, "")


def insists(text: str, lang: str) -> bool:
    line = _clean(text)
    return bool(line) and lang in _INSIST and len(line.split()) <= 10 and not line.endswith("?") and bool(_INSIST[lang].match(line))


def _where(letter: str, lang: str) -> str:
    if lang == "fr":
        return f"le disque {letter}" if letter else "ce disque"
    return f"disk {letter}" if letter else "disk sa a"


def warning(what: str, letter: str, lang: str, title: str) -> str:
    if lang == "fr":
        if what == "drive":
            return (f"Cela effacerait tout sur {_where(letter, lang)} définitivement, sans retour possible, {title}. Sauvegardez d'abord si quelque chose compte. "
                    f"Si vous êtes sûr, dites {MARK['fr']}.")
        return (f"Cela supprime tout, sans retour possible, {title}, et je copierais d'abord ce qui compte dans un endroit sûr. "
                f"Si vous êtes sûr, dites {MARK['fr']}.")
    if what == "drive":
        return (f"Sa ap efase tout sa ki sou {_where(letter, lang)} pou tout tan, san okenn mwayen pou retounen, {title}. Fè yon kopi anvan si gen anyen ki enpòtan. "
                f"Si ou sèten, di {MARK['ht']}.")
    return (f"Sa ap siprime tout bagay san okenn mwayen pou retounen, {title}, e m t ap kopye sa ki enpòtan yon kote ki an sekirite anvan. "
            f"Si ou sèten, di {MARK['ht']}.")


def acknowledgement(what: str, letter: str, lang: str, title: str) -> str:
    if lang == "fr":
        if what == "drive":
            return (f"Compris, {title}, {_CALL['fr']}. Je ne formate pas de disque d'ici, donc celui-là est à vous : clic droit sur Démarrer, Gestion des disques, "
                    f"clic droit sur {_where(letter, lang)} puis Formater. Je n'ai rien touché.")
        return (f"Compris, {title}, {_CALL['fr']}. Je ne vide pas toute une machine d'un coup, et je n'ai rien touché. Donnez-moi un dossier à la fois.")
    if what == "drive":
        return (f"Konprann, {title}, {_CALL['ht']}. Mwen pa fòma disk soti isit la, kidonk sa se pou ou: klike dwat sou Start, louvri Disk Management, "
                f"klike dwat sou {_where(letter, lang)} epi chwazi Format. Mwen pa manyen anyen.")
    return f"Konprann, {title}, {_CALL['ht']}. Mwen p ap vide tout yon machin nan yon sèl kou, e mwen pa manyen anyen. Ba m yon dosye alafwa."


def _last_text(content) -> str:
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content or "")


def _warned(history, lang: str) -> tuple:
    """(kind, letter) of the drastic line his insist answers: the last thing Jarvis said was the warning (it ends with the MARK) or the answer to an insist."""
    items = [m for m in (history or []) if isinstance(m, dict)]
    last = next((i for i in range(len(items) - 1, -1, -1) if items[i].get("role") == "assistant"), None)
    if last is None:
        return (None, "")
    said = _last_text(items[last].get("content", ""))
    if MARK[lang] not in said and not (said.startswith(_ACK_HEAD[lang]) and _CALL[lang] in said):
        return (None, "")
    for i in range(last - 1, -1, -1):
        if items[i].get("role") != "user":
            continue
        line = _last_text(items[i].get("content", ""))
        if insists(line, lang):
            continue
        return kind(line, lang)
    return (None, "")


def handle(text: str, history, lang: str, title: str) -> str | None:
    """The reply for this line in French ('fr') or Kreyol ('ht'), or None when it is not a drastic order nor an insist on a warning."""
    if lang not in _KINDS:
        return None
    try:
        if insists(text, lang):
            what, letter = _warned(history, lang)
            return acknowledgement(what, letter, lang, title) if what else None
        what, letter = kind(text, lang)
        return warning(what, letter, lang, title) if what else None
    except Exception:  # noqa: BLE001 - a heads-up is only help
        return None
