"""Talking over Jarvis: what did he mean, and what had he actually heard?

When the mic hears someone over Jarvis's reply, the voice loop (main.respond) first ducks his voice, then transcribes
what cut in and asks classify_interruption() what it was:

- "empty"       a cough, a door, a level spike with no words: he carries on (it used to stop him)
- "echo"        his own words coming back from the speakers: he carries on
- "backchannel" "mhm", "yeah", "right", "go on" while he is talking: listening noises, not a turn; he carries on and
                nothing enters the conversation. So is "okay" / "good" once he has said something this turn ("Give me a
                second, sir, I'm working on it.") and the task is still running: the task carries on.
- "status"      "what's the hold up?" while he is still working: a progress answer, the task carries on
- "interrupt"   anything else: he stops. While he is TALKING that needs two words or more, a stop word, his name or a
                one-word answer ("Yes.", "No.", "Nah.", "Go."), so a stray word from the room no longer cuts him off.

What he heard is kept separately from what was queued (audio/tts.Speaker.heard): a sentence counts as said only for
the part that played, so "what was the last thing you said?" quotes the cut point and "go on" (asks_to_resume) picks
up the rest. Kokoro gives no word timings, so a cut point is an estimate from how far its audio had played.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

from audio import echo

# Listening noises. Only these words, with nothing else, count as a backchannel ("got it", "i see", "mm-hm" split into
# words). A stop word or a command is never among them.
BACKCHANNEL_WORDS = frozenset({
    "mhm", "mmhm", "mm", "mmm", "hm", "hmm", "uh", "huh", "uhhuh", "ah", "oh", "yeah", "yea", "yep", "yup", "okay", "ok",
    "right", "sure", "got", "it", "i", "see", "alright", "cool", "nice", "wow", "true", "exactly", "indeed",
    "good", "great", "perfect",
    "wi", "anhan", "dakò", "dako", "oui", "ouais", "d'accord",          # Kreyol / French: "wi wi", "oui oui", "anhan"
})
# Always a turn, even over his voice, and never held back by the opt-in voice gate. The English stop words also live in
# main.STOP_WORDS (the voice loop passes its full list, Kreyol and French included); audio/echo._COMMANDS are the words
# the echo check always lets through ("no", "nope", "hold", "shush"), so they stop him here too.
STOP = frozenset({"stop", "wait", "hold on", "hang on", "shh", "quiet", "cancel", "never mind", "nevermind", "enough",
                  "pause", "hush", "halt", "silence", "abort", "don't", "dont", "nah", "non"}) | echo._COMMANDS
# One word that is a turn on its own while he talks: an answer to what he is asking ("Shall I go ahead?" "Yes."), or
# how the owner cuts him off (data/jarvis.log: "Yes." x10, "Go.", "Bah!" x2 right after "(interrupted)").
ANSWERS = frozenset({"yes", "go", "bah"})
_WAKE = re.compile(r"\b(?:jarvis|charvis|jervis|javis|jarvas|jarves|jarvus)\b", re.I)   # as main.WAKE_PREFIX hears it
_RESUME = re.compile(
    r"^(?:(?:ok|okay|alright|all right|yeah|right|oh|um|uh|sorry|so)[,.!]?\s+)*"
    r"(?:go on|continue|carry on|keep going|please continue|please go on|what were you saying|you were saying|"
    r"as you were saying|finish (?:what you were saying|your sentence|your thought)|sorry,? go ahead|"
    r"kontinye|continuez)"
    r"(?:[,.]?\s+(?:jarvis|sir|please|then))*[\s.!?]*$", re.I)


# After a reply the spoken budget shortened (core.speech.spoken_budget): his bare ask for the rest. Never "tell me more
# about X" (a new question) and never a bare "yes" (it answers whatever was asked).
_MORE = re.compile(
    r"^(?:(?:ok|okay|alright|all right|yeah|yes|right|oh|um|uh|so|and|sure|hey jarvis|jarvis)[,.!]?\s+)*"
    r"(?:tell me more|more|say more|more please|more detail|more details|give me more|what else|and\??|and then|then what|"
    r"elaborate|go deeper|keep going|go on|continue|carry on|finish (?:it|that))"
    r"(?:[,.]?\s+(?:jarvis|sir|please|then))*[\s.!?]*$", re.I)


def asks_for_more(text: str) -> bool:
    """'tell me more', 'and?', 'what else?', 'go on': the rest of an answer the spoken budget kept back."""
    t = re.sub(r"^\W*\[[^\]]*\]\s*", "", (text or "").strip())
    return bool(_MORE.match(t))


def _words(text: str) -> list[str]:
    """Words as Whisper wrote them, accents kept ("Arrête", "dakò"), case and apostrophe style ignored."""
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", (text or "").casefold().replace("’", "'"))


def asks_to_resume(text: str) -> bool:
    """'go on', 'continue', 'what were you saying?', 'sorry, go ahead': pick up where he was cut off."""
    t = re.sub(r"^\W*\[[^\]]*\]\s*", "", (text or "").strip())
    return bool(_RESUME.match(t))


def is_backchannel(text: str) -> bool:
    words = _words(text)
    return bool(words) and len(words) <= 4 and all(w in BACKCHANNEL_WORDS for w in words)


def is_stop(asked: str, stop_words: Iterable[str] | None = None) -> bool:
    """A stop word ("Stop!", "No.", "Arrête.") or his name: these always stop him."""
    norm = " ".join(_words(asked))
    stops = set(stop_words) | STOP if stop_words is not None else STOP
    return bool(norm) and bool(_WAKE.search(asked or "") or norm in stops or norm.replace("'", "") in stops)


def classify_interruption(asked: str, speaking: bool, worker_alive: bool, spoken: Iterable[str] = (),
                          stop_words: Iterable[str] | None = None, earlier: Iterable[str] = ()) -> str:
    """'empty' | 'echo' | 'backchannel' | 'status' | 'interrupt' for what cut in (see the module docstring).

    speaking: his voice was playing when it cut in. worker_alive: the reply is still being worked out.
    spoken: the sentences he has played this turn (the echo check; empty before his first word).
    stop_words: the voice loop's full list. earlier: other sentences he played seconds ago (audio.echo.context), whose
    words are taken out of `asked` but never make it echo on their own."""
    words = _words(asked)
    if not words:
        return "empty"
    spoken = list(spoken)
    # "Yeah. Understood, sir." over his "Understood, sir.": his words are taken out and only the owner's part is judged
    earlier = list(earlier)
    asked = echo.owner_part(asked, spoken, earlier) if spoken or earlier else asked
    words = _words(asked)
    if not words:
        return "echo"
    if worker_alive:
        from core import progress
        if progress.is_status_question(asked):
            return "status"
    if is_stop(asked, stop_words):
        return "interrupt"
    if not speaking:
        if worker_alive and spoken and is_backchannel(asked):
            return "backchannel"                # "okay" after "Give me a second, sir...": the task carries on
        return "interrupt"                      # nothing said yet, or the reply is over: it is his turn ("okay" too)
    if is_backchannel(asked) or asks_to_resume(asked):
        return "backchannel"
    if len(words) < 2 and " ".join(words) not in ANSWERS:
        return "backchannel"                    # one stray word over his voice: not enough to stop him
    return "interrupt"


def heard_prefix(text: str, fraction: float) -> str:
    """The words of `text` he heard when its audio stopped `fraction` of the way through: whole words only."""
    text = (text or "").strip()
    if fraction >= 1.0:
        return text
    if fraction <= 0.0 or not text:
        return ""
    cut = int(len(text) * fraction)
    head = text[:cut]
    if cut < len(text) and not text[cut].isspace() and not head.endswith(" "):
        space = head.rfind(" ")
        head = head[:space] if space > 0 else ""       # the word being spoken was not heard whole
    return head.rstrip(" ,;:-—")


def resumed_from(rest: str) -> str:
    """The cut-off remainder as the continuation of a sentence: 'Then the GPU...' -> 'then the GPU...'."""
    rest = (rest or "").strip()
    if len(rest) > 1 and rest[0].isupper() and rest[1].islower() and not rest.startswith(("I ", "I'")):
        return rest[0].lower() + rest[1:]
    return rest


def owner_only() -> bool:
    """JARVIS_BARGE_IN_VOICE=1: with voices enrolled, only the person Jarvis is talking to can cut him off while he
    speaks (the TV and the room cannot). Off by default; the voice score is never shown to the model."""
    return os.getenv("JARVIS_BARGE_IN_VOICE", "0").strip() == "1"
