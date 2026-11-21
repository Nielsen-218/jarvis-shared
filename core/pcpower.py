"""Exact owner PC power commands from phone, voice or desktop.

Strict full-utterance matching: unrelated devices, quotes, questions and model
instructions must never become PC shutdown/wake authorization.
"""
from __future__ import annotations
import re

_PREFIX=re.compile(r"^\s*(?:(?:hey|okay|ok)\s+)?jarvis[\s,.:!]+",re.I)
_DEVICE=r"(?:(?:my|the)\s+)?(?:pc|computer|desktop|machine)"
_POLITE=r"(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)*"
# Ordered from most specific action to least. Captures optional 'now'.
_COMMANDS=(
 ("wake",rf"(?:turn|switch|power)\s+on\s+{_DEVICE}|"
         rf"(?:turn|switch|power)\s+{_DEVICE}\s+on|"
         rf"(?:wake(?:\s+up)?|boot|start\s+up)\s+{_DEVICE}|"
         rf"wake\s+{_DEVICE}\s+up"),
 ("shutdown",rf"(?:turn|switch|power)\s+off\s+{_DEVICE}|"
             rf"(?:turn|switch|power)\s+{_DEVICE}\s+off|"
             rf"(?:shut\s*down|power\s*down)\s+{_DEVICE}|"
             rf"(?:shut\s+{_DEVICE}\s+down)"),
 ("restart",rf"(?:restart|reboot)\s+{_DEVICE}"),
 ("sleep",rf"(?:put\s+{_DEVICE}\s+to\s+sleep|sleep\s+{_DEVICE}|(?:make|let|have)\s+{_DEVICE}\s+go\s+to\s+sleep)"),
 ("lock",rf"(?:lock)\s+{_DEVICE}"),
 ("sign_out",rf"(?:sign\s*out|log\s*out|log\s*off)\s+(?:of\s+)?{_DEVICE}"),
 ("cancel",rf"(?:cancel|stop)\s+(?:the\s+)?(?:shutdown|restart)\s+(?:of\s+)?{_DEVICE}"),
)

def parse(message:str)->tuple[str,bool]|None:
    """Return (action, immediately) from a complete, unambiguous PC command."""
    if not isinstance(message,str) or len(message)>350:return None
    text=message.strip()
    text=re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*","",text,flags=re.I)
    text=_PREFIX.sub("",text,count=1)
    text=re.sub(r"[.!?]+\s*$","",text).strip()
    text=re.sub(r"^(?:(?:please|can you|could you|would you)\s+)*","",text,flags=re.I)
    text=re.sub(r"\s+(?:please|for me)$","",text,flags=re.I).strip()
    immediate=bool(re.search(r"\s+(?:right\s+)?(?:now|immediately)$",text,re.I))
    if immediate:
        text=re.sub(r"\s+(?:right\s+)?(?:now|immediately)$","",text,flags=re.I)
    for action,pattern in _COMMANDS:
        if re.fullmatch(pattern,text,flags=re.I):
            return action,immediate
    return None
