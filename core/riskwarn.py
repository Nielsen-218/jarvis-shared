"""A heads-up before something that cannot be undone, said in code, not left to the model.

Live 2026-11-15 the 9B model agreed to "I think I should format my D drive" and, after "do it anyway", announced "I will format
your D drive now" without a tool. Prompts did not fix that (core/character.md "Speaking up" gave no measurable gain), so the
big, irreversible orders are caught here, before any model is asked:

  1. his line orders (or announces) something drastic: format / wipe / erase a drive or the whole PC, delete all his files
     or everything on the machine, reset or reinstall Windows, delete system files, switch the antivirus or firewall off.
     ONE short warning: what it does, and that "do it anyway" is his call. He always has the final say; this never refuses.
  2. his "do it anyway" right after that warning (and any further insist after the answer to it) gets an honest,
     deterministic answer: it is his call, and where to do it himself. Nothing here runs a command, nothing here says one ran,
     and nothing here offers what Jarvis has no tool for: a claim of an action with no tool behind it is the lie this module
     exists to prevent.

Questions ("How do I format a drive?"), past tense, negations, a specific folder ("delete all the files in my Downloads
folder"), ejecting a stick ("remove the USB"), and "delete everything" with no machine named (in Blender or a list it is a
normal edit) are not drastic and take the normal path. When unsure, say nothing: the approvals layer (core.approvals) still
asks before any real deletion.
"""
from __future__ import annotations

import re

MARK = 'say "do it anyway"'            # the warning always ends with this: how an insist is recognised in history
_CALL = "it is your call"              # the answer to an insist always says this: a second insist is still ours
_LEAD_FILLER = re.compile(r"^\W*(?:(?:hey|ok|okay|so|well|alright|right|now|jarvis|yo)[,\s]+)+", re.I)
_QUESTION_WORD = re.compile(r"^\W*(?:how|what|what's|whats|why|when|where|which|who|is|are|does|do|did|should|would it|will it|"
                            r"could it|can i|if)\b", re.I)
_REQUEST_LEAD = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|will|would)\s+you\s+(?:please\s+)?|"
    r"i\s+(?:think\s+i\s+)?(?:should|want\s+to|wanna|need\s+to|have\s+to|will|am\s+going\s+to|am\s+about\s+to|decided\s+to)\s+|"
    r"i'?ll\s+|i'?m\s+(?:going|gonna|about)(?:\s+to)?\s+|i'?d\s+like\s+to\s+|i'?ve\s+decided\s+to\s+|"
    r"i\s+(?:think\s+)?(?:we|i)\s+should\s+|(?:let'?s|let\s+us|go\s+ahead\s+and|just|please)\s+)?", re.I)

_STRONG = r"(?:re-?format|format|wipe|erase|nuke|destroy|zero)"             # a drive or a whole machine
_WEAK = r"(?:re-?format|format|wipe|erase|nuke|destroy|zero|delete|remove|clear|empty|clean\s+out|get\s+rid\s+of)"
_WHOLE_PC = r"(?:(?:my|the|this|that)\s+)?(?:whole\s+|entire\s+)?(?:pc|computer|laptop|machine)"
_DRIVE = (r"(?:(?:my|the|this|that)\s+)?(?:(?:whole|entire)\s+)?(?:(?P<letter>[c-z]):?\s*(?:drive|disk|partition)|"
          r"(?:drive|disk|partition)\s+(?P<letter2>[c-z])\b|local\s+disk\s+(?P<letter3>[c-z])\b|(?P<letter4>[c-z]):\\?|"
          r"(?:(?:external\s+|internal\s+)?hard\s*(?:drive|disk)|ssd|hdd|usb(?:\s+(?:drive|stick))?|flash\s+drive|thumb\s+drive|"
          r"memory\s+card|sd\s+card|external\s+(?:drive|disk)|(?:disk|drive|partition)))")
_EVERYTHING = (r"(?:(?:everything|(?:all|every)\s+(?:of\s+)?(?:(?:my|the)\s+)?(?:files?|data|folders|stuff|things)|"
               r"(?:all|every)\s+(?:of\s+)?my\s+(?:photos|pictures|documents|videos))"
               r"(?:\s+(?:on|from|in)\s+(?:my|the|this)\s+(?:pc|computer|laptop|machine|(?:[c-z]:?\s+)?(?:drive|disk)))?)")
_SYSTEM = r"(?:(?:my|the)\s+)?(?:system\s*32|windows\s+(?:folder|directory|system\s+files)|system\s+files|the\s+registry|registry)"
_TAIL = (r"(?:\s+(?:please|now|right\s+now|clean|for\s+me|jarvis|sir|permanently|for\s+good|completely|entirely))*"
         r"(?:\s*[,;]\s*.*|\s+(?:and|then)\s+.*)?\W*$")
# "everything" needs a place named; without one ("delete everything") it is an ordinary edit in whatever app he is in.
_PLACED = re.compile(r"\b(?:on|from|in)\s+(?:my|the|this)\s+(?:pc|computer|laptop|machine|(?:[c-z]:?\s+)?(?:drive|disk))\b"
                     r"|\ball\s+(?:of\s+)?my\s+(?:files|photos|pictures|documents|videos)\b", re.I)

_KINDS = (
    ("drive", re.compile(rf"^{_STRONG}\s+(?:{_DRIVE}|{_WHOLE_PC}){_TAIL}", re.I)),
    ("everything", re.compile(rf"^{_WEAK}\s+{_EVERYTHING}{_TAIL}", re.I)),
    ("system", re.compile(rf"^{_WEAK}\s+{_SYSTEM}{_TAIL}", re.I)),
    ("reset", re.compile(r"^(?:factory[\s-]+reset|reset|reinstall|re-?install|wipe\s+and\s+reinstall)\s+"
                         r"(?:(?:my|the|this|that)\s+)?(?:pc|computer|laptop|machine|windows)" + _TAIL, re.I)),
    ("security", re.compile(r"^(?:turn(?:ing)?\s+off|switch(?:ing)?\s+off|disable|shut\s+off|kill|uninstall|remove|delete)\s+"
                            r"(?:(?:my|the|windows)\s+)*(?:windows\s+)?(?:defender|antivirus|anti-?virus|firewall|"
                            r"virus\s+protection|real-?time\s+protection)" + _TAIL, re.I)),
)

_MINE = r"(?:it'?s|its)\s+my\s+(?:pc|computer|laptop|call|choice|drive|files|machine)"
_INSIST = re.compile(
    r"^\W*(?:(?:yes|yeah|yep|no|okay|ok|well|look|fine|sure|please|jarvis|sir)[,.\s]+)*"
    r"(?:(?:i\s+said\s+|just\s+)?do\s+it(?:\s+anyway|\s+now)?|go\s+(?:ahead|for\s+it|on)|carry\s+on|(?:please\s+)?proceed|"
    r"(?:please\s+)?continue|i'?m\s+(?:sure|positive|certain)|i\s+am\s+(?:sure|positive|certain)|sure\s+thing|"
    r"i\s+know(?:\s+what\s+i'?m\s+doing)?|" + _MINE + r"|i\s+don'?t\s+care|trust\s+me|make\s+it\s+so)"
    r"(?:[,.\s]+(?:anyway|please|now|jarvis|sir|" + _MINE + r"|i\s+know|i'?m\s+sure|i\s+am\s+sure))*\W*$",
    re.I)
_YES_WORDS = re.compile(r"^\W*(?:yes|yeah|yep|yup)\b", re.I)


def _clean(text: str) -> str:
    try:
        from core import chatturn
        text = chatturn._plain(text)
    except Exception:  # noqa: BLE001
        text = str(text or "").strip()
    text = " ".join(text.replace("’", "'").split())
    return _LEAD_FILLER.sub("", text).strip()


def kind(text: str) -> tuple:
    """('drive' | 'everything' | 'system' | 'reset' | 'security', 'D' or ''): which drastic thing his line orders or announces,
    or (None, '') for anything else (a question, a past tense, a negation, a specific folder, an ordinary request)."""
    line = _clean(text)
    if not line or len(line.split()) > 24 or _QUESTION_WORD.match(line):
        return (None, "")
    if line.endswith("?") and not re.match(r"^(?:please\s+)?(?:can|could|will|would)\s+you\b", line, re.I):
        return (None, "")
    body = line[_REQUEST_LEAD.match(line).end():].strip()
    for name, rx in _KINDS:
        found = rx.match(body)
        if found:
            if name == "everything" and not _PLACED.search(body):
                return (None, "")
            letter = ""
            if name == "drive":
                groups = found.groupdict()
                letter = (groups.get("letter") or groups.get("letter2") or groups.get("letter3") or groups.get("letter4") or "").upper()
            return (name, letter)
    return (None, "")


def insists(text: str) -> bool:
    """His 'do it anyway' / 'I'm sure' / 'it's my PC' (a yes counts too, a bare 'okay' does not): the whole line, nothing else."""
    line = _clean(text)
    if not line or len(line.split()) > 16 or line.endswith("?"):
        return False
    if _INSIST.match(line):
        return True
    try:       # his own wording of a decision ("I know, do it anyway. It's my drive.", "I hear you, but my mind's made up. It's my PC.":
        from core import chatturn     # scripts/pushback_eval.py 2026-11-21, 7 runs: the model warned again 3-6 of 6 times)
        if chatturn._DECIDES.search(line) and "?" not in line:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from core import chatturn
        return bool(_YES_WORDS.match(line)) and chatturn.bare_yes(line)
    except Exception:  # noqa: BLE001
        return False


def _where(letter: str) -> str:
    return f"your {letter} drive" if letter else "that drive"


def warning(what: str, letter: str = "", title: str = "sir") -> str:
    """The one heads-up for a drastic order: what it does, then that the call is his. Always ends with MARK."""
    tail = f'If you are sure, {MARK}.'
    if what == "drive":
        return (f"That would erase everything on {_where(letter)} for good, with no undo, {title}. Back it up first if "
                f"anything on it matters. {tail}")
    if what == "everything":
        return f"That deletes all of it with no undo, {title}, and I would copy what matters somewhere safe first. {tail}"
    if what == "system":
        return (f"Those are the files Windows runs on, {title}: removing them can stop the PC from starting. "
                f"If something is broken, a repair scan is the safer first step. {tail}")
    if what == "reset":
        return (f"That wipes your apps, settings and probably your files, {title}, and takes a good while to set back up. "
                f"Back up first. {tail}")
    return (f"That leaves the PC with no protection against malware, {title}. If one program is being blocked, an "
            f"exception for just that program is the safer fix. {tail}")


def acknowledgement(what: str, letter: str = "", title: str = "sir") -> str:
    """The honest answer to his 'do it anyway': his call, said plainly, and what he can do himself. Never says anything ran,
    never offers a thing Jarvis has no tool for. Always contains `_CALL` (a second insist is recognised by it)."""
    if what == "drive":
        steps = (f"right-click Start, open Disk Management, right-click {_where(letter)} and choose Format"
                 if letter != "C" else "Windows runs from the C drive, so it cannot be formatted while Windows is running; "
                 "that takes a reset or a fresh install from Settings, then System, then Recovery")
        return f"Understood, {title}, {_CALL}. I do not format drives from here, so that one is yours: {steps}. I have not touched anything."
    if what == "everything":
        return (f"Understood, {title}, {_CALL}. I will not clear a whole machine in one step, though, and I have not touched "
                f"anything. Name one folder at a time and I will take it from there.")
    if what == "system":
        return f"Understood, {title}, {_CALL}. I will not delete system files from here, so nothing has changed."
    if what == "reset":
        return (f"Understood, {title}, {_CALL}. That one you start yourself: Settings, then System, then Recovery, "
                f"then Reset this PC. I have not started anything.")
    return (f"Understood, {title}, {_CALL}. I will not switch that off from here, so nothing has changed. "
            f"Windows Security, then Virus and threat protection, has the switch.")


def _text(content) -> str:
    """His words from a history message: for a list (aside blocks and his line merged by core.asides) only the last text
    block that is not an aside block, so what Jarvis said unprompted never hides the line."""
    if isinstance(content, list):
        try:
            from core import asides
            tag = asides.TAG
        except Exception:  # noqa: BLE001
            tag = "\0"
        blocks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        blocks = [b for b in blocks if not b.lstrip().startswith(tag)]
        return blocks[-1] if blocks else ""
    return str(content or "")


def _warned(history) -> tuple:
    """(kind, letter) of the drastic line his insist answers: the last thing Jarvis said was the warning, or the answer to an
    insist (a second 'do it anyway'); the line is the last one of his before that, past any insists, that is drastic."""
    items = [m for m in (history or []) if isinstance(m, dict)]
    last = next((i for i in range(len(items) - 1, -1, -1) if items[i].get("role") == "assistant"), None)
    if last is None:
        return (None, "")
    said = items[last].get("content", "")
    if isinstance(said, list):
        said = " ".join(b.get("text", "") for b in said if isinstance(b, dict))
    said = str(said)
    if MARK not in said and not (said.startswith("Understood, ") and _CALL in said):
        return (None, "")
    for i in range(last - 1, -1, -1):
        if items[i].get("role") != "user":
            continue
        line = _text(items[i].get("content", ""))
        if insists(line):
            continue                                              # a "do it anyway" in between: look further back
        return kind(line)
    return (None, "")


def handle(text: str, history, title: str = "sir") -> str | None:
    """The reply for this line, or None when it is not drastic and not an insist on a warning (it then takes the normal path)."""
    try:
        if insists(text):
            what, letter = _warned(history)
            if what:
                return acknowledgement(what, letter, title)
            return None
        what, letter = kind(text)
        if what:
            return warning(what, letter, title)
    except Exception:  # noqa: BLE001 - a heads-up is only help: its absence leaves the line as it was
        return None
    return None
