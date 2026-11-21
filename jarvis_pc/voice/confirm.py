"""Voice (or chat) confirmations for the gate. Fail closed: only a clear yes approves; silence, noise and "maybe" do not.

VoiceApprover plugs into Guardrails(approver=...). The speak/listen functions are passed in, so this works with any
TTS/STT (or a chat box) and is fully testable without audio.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

YES = {"yes", "yeah", "yep", "yup", "yes please", "sure", "okay", "ok", "do it", "go ahead", "confirm", "confirmed",
       "approved", "allow", "allow it", "yes do it", "wi", "oui", "dakò", "dako"}
NO = {"no", "nope", "nah", "stop", "cancel", "dont", "do not", "deny", "refuse", "abort", "wait", "non"}
_NO_WORDS = {"no", "nope", "stop", "cancel", "dont", "deny", "abort", "wait", "not", "non"}
_YES_WORDS = {"yes", "yeah", "yep", "confirm", "approved", "oui", "wi"}
_FILLER = re.compile(r"^(jarvis|hey jarvis|ok jarvis|okay jarvis)[,\s]+|[,\s]+(jarvis|please|sir)$")


def parse_confirmation(text: Optional[str]) -> Optional[bool]:
    """True = clear yes, False = clear no, None = unclear or empty.
    Mixed answers ("yes but wait", "maybe", noise) are unclear: ambiguity never approves."""
    t = re.sub(r"[.!?,]+$", "", (text or "").lower().strip()).replace("'", "")
    for _ in range(2):
        t = _FILLER.sub("", t).strip()
    if not t:
        return None
    if t in YES:
        return True
    if t in NO:
        return False
    words = set(re.findall(r"[a-zà-ÿ]+", t))
    says_no, says_yes = bool(words & _NO_WORDS), bool(words & _YES_WORDS)
    if says_no and not says_yes:
        return False
    return None


class VoiceApprover:
    def __init__(self, speak: Callable[[str], None], listen: Callable[[], Optional[str]], retries: int = 1):
        self.speak, self.listen, self.retries = speak, listen, retries
        self.log: List[Tuple[str, Optional[str], bool]] = []

    def __call__(self, req) -> bool:
        why = "; ".join(req.reasons[:2])
        self.speak(f"I need your permission: {req.command}. {why}. Say yes to allow, or no.")
        for attempt in range(self.retries + 1):
            try:
                reply = self.listen()
            except Exception:  # noqa: BLE001
                reply = None
            verdict = parse_confirmation(reply)
            if verdict is not None:
                self.log.append((req.command, reply, verdict))
                return verdict
            if attempt < self.retries:
                self.speak("Sorry, I need a clear yes or no.")
        self.log.append((req.command, None, False))
        return False  # no clear answer is a no
