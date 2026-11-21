"""Daily Awareness by voice/chat (Issue #7): recall the last 24 hours, control room listening, forget.

The transcript and its rules live in core/awareness.py. These tools let Jarvis answer "what were we talking
about at 8 this morning?" from real timestamped lines — and say so when there's nothing.
"""
from core.words import plural
from tools.registry import tool


@tool(
    "THE tool for anything said today: recall what was said in the last 24 hours, from the timestamped Daily Awareness transcript (conversations "
    "with Jarvis, plus room speech when Daily Awareness listening was on). Use for 'what were we talking about "
    "at 8 this morning?', 'what did I say about the car earlier?', 'what did we decide an hour ago?'. Answer ONLY "
    "from the returned lines and mention the times; if it says there's nothing, say so — never guess.",
    {"around": {"type": "string", "description": "When, as said: '8 am', '8 this morning', '3:30 pm', 'an hour ago', "
                                                 "'this morning', 'last night', or '' for the whole day"},
     "query": {"type": "string", "description": "Optional topic words to narrow it down, e.g. 'car insurance'"}},
)
def recall_today(around: str = "", query: str = "") -> str:
    from core import awareness
    return awareness.recall(around, query)


@tool(
    "Turn Daily Awareness room listening on/off: while on, Jarvis keeps a 24-hour transcript of what's said in "
    "the room (the PC microphone), so it can answer questions about it later. Before 'start', ASK whether "
    "everyone in the room knows Jarvis will be listening, and pass everyone_knows=true only after they say yes. "
    "guests_agreed=true keeps voices that aren't household members too (otherwise only known voices are kept).",
    {"action": {"type": "string", "enum": ["start", "pause", "resume", "stop", "status"]},
     "everyone_knows": {"type": "boolean"}, "guests_agreed": {"type": "boolean"}},
    required=["action"],
)
def daily_awareness(action: str, everyone_knows: bool = False, guests_agreed: bool = False) -> str:
    from core import awareness, pcbridge, users
    try:
        pcbridge.require_owner(users.current())
        s = awareness.session_anywhere() if action == "status" else \
            awareness.control_anywhere(action, guests_agreed, everyone_knows)
    except ValueError as error:
        return str(error)
    except pcbridge.PCError as error:
        return str(error)
    state = {"on": "on — I'm keeping a 24-hour transcript of the room", "paused": "paused",
             "off": "off"}.get(s["state"], s["state"])
    who = " (guests included)" if s.get("guests") and s["state"] == "on" else \
          " (only household voices are kept)" if s["state"] == "on" else ""
    return f"Daily Awareness room listening is {state}{who}."


@tool(
    "Forget part of the 24-hour Daily Awareness transcript: the last N minutes, or everything. Always do it when "
    "asked ('forget the last ten minutes', 'delete today's transcript'); nothing older than 24 hours is kept anyway.",
    {"last_minutes": {"type": "integer", "description": "Forget the last N minutes (0 = not by time)"},
     "everything": {"type": "boolean"}},
)
def forget_today(last_minutes: int = 0, everything: bool = False) -> str:
    from core import awareness
    if not everything and not (last_minutes and last_minutes > 0):
        return "Say how much to forget: the last few minutes, or everything."
    scope = "everything from the last 24 hours" if everything else f"the last {plural(last_minutes, 'minute')}"
    try:
        n, complete = awareness.delete_everywhere(minutes=last_minutes or None, everything=everything)
    except awareness.ThreadNotForgotten as kept:
        tail = "" if kept.complete else " One of my machines was offline, so its copy will simply age out within 24 hours."
        return (f"I deleted {scope} from today's transcript, but a conversation thread file was busy, so I may still "
                f"remember those words for now. Ask me again in a moment and I'll finish it.{tail}")
    tail = "" if complete else " One of my machines was offline, so its copy will simply age out within 24 hours."
    return f"Done — I forgot {scope} ({plural(n, 'line')}).{tail}"
