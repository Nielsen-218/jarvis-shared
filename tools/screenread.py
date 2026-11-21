"""Free, local screen reading tool: OCR + Windows UI Automation, no model at all.

This is the first choice for "what's on my screen" questions about TEXT. It returns text, so the
model answers from local reading instead of a vision call. look_at_screen (the local vision model,
or the cloud tier's) is for what reading can't tell: what it looks like, images, layout, colours.
"""
from tools.registry import tool


@tool(
    "Read the TEXT on the screen with local OCR + Windows UI Automation (no model). Use this FIRST "
    "whenever the user refers to on-screen text, an error, a menu or a control ('read this', 'what "
    "does this say', 'find the Save button'). Use look_at_screen instead when they ask what you SEE "
    "or how it looks (layout, images, colours, a game or video), or when this returns no useful text. "
    "Leave both arguments empty unless they named an app or a text to find.",
    {"app": {"type": "string", "description": "optional process/app name to focus on, e.g. 'notepad.exe'"},
     "find_text": {"type": "string", "description": "optional: locate this on-screen text/control and return its position"}},
    required=[],
)
def read_screen(app: str = "", find_text: str = "") -> str:
    from core import observation, pcvision

    # The same screen read twice within seconds ("what's on my screen?" ... "and the error?") reuses the fresh
    # observation instead of another 1-2 s OCR + UI Automation pass; the reply says how old it is.
    fresh = observation.latest("screen_text", key=app or "")
    if fresh and fresh["available"]:
        data = fresh["value"]
        lines = [data["text"], observation.describe(fresh)]
    else:
        data = pcvision.read_screen(process=app or None)
        confidence = min(1.0, (len(data.get("ocr", [])) + len(data.get("uia", []))) / 40) if data["sources"] else 0.0
        observation.record("screen_text", data, source="+".join(data["sources"]) or "none", confidence=confidence,
                           key=app or "", available=bool(data["sources"]))
        lines = [data["text"]]
    if data["sources"]:
        lines.append(f"(read locally via {', '.join(data['sources'])}; no paid AI used)")
    if find_text:
        match = pcvision.find(find_text, data)
        if match:
            lines.append(f"Found {find_text!r}: {match['text']!r} at {match['center']} (via {match['source']}).")
        else:
            lines.append(f"Did not find {find_text!r} on screen.")
    if not data["sources"]:
        lines.append("Nothing readable locally — if you still need to see it, use look_at_screen (paid vision).")
    return "\n".join(lines)
