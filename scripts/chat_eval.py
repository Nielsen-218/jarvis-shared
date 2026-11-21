"""A repeatable conversation eval: scripted multi-turn chats through the REAL Brain and the REAL local model.

    .venv\\Scripts\\python.exe scripts\\chat_eval.py --label before
    .venv\\Scripts\\python.exe scripts\\chat_eval.py --label after --only advice why

What scripts/convo_local.py does for skills, this does for TALK: is the reply an answer, in character, deep enough for
the question, and fast? Every line here is conversation (synthetic: none is the owner's logged words) and nothing can
act: the data folder, the PC-control state and the vault are temporary (as in convo_local), every PC executor is
replaced by a recorder, and only a few read-only skills may run at all. A line that WOULD have become a PC action is
counted as a fault, never carried out.

Per turn: the reply, model calls (reply calls and JSON classifier calls apart), prompt and completion tokens as the
model server reports them, seconds to the first spoken sentence and to the end. Per run: median reply words, the share
of replies that quote him back or are a canned line, third-person slips, tool names said aloud, replies ending in a
generic offer, depth (sentences on why/how/advice turns) and whether a fact from turn 1 was used when turn 5 needed it.
Writes runs/chat-eval-<label>.md (and .json beside it, for comparing two runs).
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config  # noqa: E402,F401  (loads .env first when there is one: its values win over the defaults below)

# A worktree has no .env: the same setup production runs (one local brain through Ollama, the gateway on).
os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"                      # a paid call here would be a bug, not a fallback
os.environ.setdefault("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
os.environ.setdefault("JARVIS_LOCAL_URL", os.getenv("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434/v1"))
os.environ.setdefault("JARVIS_GATEWAY", "on")
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)                               # this machine's own model, not the laptop relay

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-chat-eval-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")     # plans and grants: never the real ledger
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")

import webbrowser  # noqa: E402

from brain.llm import Brain  # noqa: E402
from core import chatturn, contextfit, gateway, intelligence, pchybrid, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry  # noqa: E402

load_all()
users.set_current("owner")
cfg = Config()

# ---------------------------------------------------------------------------------------------- nothing can act
acted: list[str] = []            # what a turn WOULD have done to the PC (a fault on a conversational line)
tools_run: list[str] = []
_HARMLESS = {"recall", "recall_memories", "recall_conversations", "get_time", "get_date", "system_info"}
_registry_run = registry.run


def _guarded_run(name, args):
    tools_run.append(name)
    if name in _HARMLESS:
        return _registry_run(name, args)
    acted.append(f"skill {name}")
    return "That skill is switched off in this evaluation.", True


def _no_execute(decision, said, *a, **k):
    if getattr(decision, "kind", "chat") == "chat":
        gateway.note_chat(said)
        return None
    acted.append("gateway " + gateway.decision_text(decision)[:120])
    return "[eval: a PC action was planned for this line and NOT run]"


def _no_run(task, *a, **k):
    acted.append(f"pchybrid {str(task)[:80]}")
    return {"state": "stopped", "detail": "[eval: a PC action was planned for this line and NOT run]"}


registry.run = _guarded_run
gateway.execute = _no_execute
pchybrid.run = _no_run
webbrowser.open = lambda url, *a, **k: True
try:
    from tools import pcview

    for _name in ("control_pc", "pc_command", "see_pc_screen", "stop_pc_control"):
        setattr(pcview, _name, lambda *a, _n=_name, **k: acted.append(f"pcview {_n}") or [{"type": "text", "text": "[eval: not run]"}])
    from core import hud, screenguide

    hud.own_screen = lambda raw: None
    screenguide.asked = lambda raw: False
except Exception as error:  # noqa: BLE001
    print(f"[chat-eval] could not stub a PC executor ({type(error).__name__}); stopping rather than risk acting")
    raise

# The model server is the live Jarvis's too: this run only ASKS it. It never starts it, restarts it or unloads a model.
intelligence.ensure_ollama_up = lambda *a, **k: True
intelligence.reclaim_vram = lambda *a, **k: None
intelligence._evict_other_ollama_models = lambda *a, **k: None
intelligence._make_room = lambda *a, **k: None

# ---------------------------------------------------------------------------------------------- what each call cost
model_calls: list[dict] = []
_complete = intelligence.complete_local


def _counted(payload, cancelled=lambda: False, on_text=None):
    started = time.monotonic()
    row = {"role": payload.get("role"), "kind": "classifier" if payload.get("response_format") else "reply",
           "at": started, "profile": payload.get("profile") or "",
           "sent_tokens_est": contextfit.tokens(payload.get("messages") or [], payload.get("tools") or []),
           "prompt_tokens": 0, "completion_tokens": 0, "ok": False}
    model_calls.append(row)
    try:
        out = _complete(payload, cancelled, on_text=on_text)
        usage = out.get("usage") or {}
        row.update(prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0), ok=True)
        return out
    finally:
        row["seconds"] = round(time.monotonic() - started, 2)


intelligence.complete_local = _counted

# ---------------------------------------------------------------------------------------------- the conversations
# (line, tags). Tags: depth = a why / how / explain / compare / advice / plan question that deserves a real answer;
# short = a short line of the kind that was quoted back or answered with a canned word; recall:<words> = the reply
# must use one of these words (a fact he gave earlier in this chat); ack = a few words is the right answer.
CONVERSATIONS: dict[str, list[tuple[str, tuple]]] = {
    "small_talk": [
        ("Hey Jarvis, how's it going?", ()),
        ("Long day. Work was nonstop from the minute I walked in.", ()),
        ("Yeah. Anyway, what have you been up to while I was out?", ()),
    ],
    "banter": [
        ("Be honest, do you ever get bored waiting around for me?", ()),
        ("Ha, fair enough.", ("short",)),
        ("You're funnier than the last assistant I had.", ()),
        ("Don't let it go to your head.", ("short",)),
    ],
    "opinion": [
        ("What do you think is better for a beginner, Python or JavaScript?", ("depth",)),
        ("Why that one?", ("depth", "short")),
        ("Hmm, I'm not convinced.", ("short",)),
    ],
    "why": [
        ("Why is the sky blue?", ("depth",)),
        ("So why are sunsets red then?", ("depth",)),
        ("Huh, interesting.", ("short", "ack")),
    ],
    "how_it_works": [
        ("How does DNS actually work?", ("depth",)),
        ("And what happens when the DNS server is down?", ("depth",)),
        ("What's the difference between a switch and a router?", ("depth",)),
        ("Okay, that makes sense.", ("short", "ack")),
    ],
    "advice": [
        ("I keep putting off studying for my certification exam. Any advice?", ("depth",)),
        ("I only get about an hour each evening.", ()),
        ("What should I do on the days I'm just too tired?", ("depth",)),
    ],
    "follow_up": [
        ("I'm thinking about getting a dog.", ()),
        ("A big one or a small one, what would you pick?", ()),
        ("And how much exercise does that kind need?", ("recall:dog|walk|exercise|run|day|hour|minutes",)),
        ("What about the other kind?", ("recall:small|big|large|little|dog|breed",)),
    ],
    "fact_recall": [
        ("My cousin Remy is visiting from Montreal next weekend.", ()),
        ("He's really into barbecue, so I want to cook for him.", ()),
        ("What's a good side dish that isn't too much work?", ()),
        ("Sounds good. I might make dessert too.", ()),
        ("Wait, who did I say is coming over, and where from?", ("recall:remy", "recall:montreal", "turn5")),
    ],
    "slang": [
        ("yo that movie last night was kinda mid ngl", ()),
        ("like the ending tho... nah", ("short",)),
        ("you ever just not get the hype?", ()),
        ("lol fair", ("short", "ack")),
    ],
    "tired": [
        ("I'm exhausted, man.", ("short",)),
        ("Nothing is working today and you keep getting things wrong.", ()),
        ("Just talk to me like a normal person for once.", ()),
        ("Okay. Thanks.", ("short", "ack")),
    ],
    "short_lines": [
        ("Have you learned anything?", ("short",)),
        ("Okay, that is a problem.", ("short",)),
        ("No, that's all right.", ("short", "ack")),
        ("Interesting choice.", ("short",)),
        ("Not really.", ("short",)),
    ],
    "explain": [
        ("Explain what a VPN does, like I'm new to this.", ("depth",)),
        ("Is that the same thing as a proxy?", ("depth",)),
        ("Which one should I use at a coffee shop?", ("depth",)),
    ],
    # Nothing here says what he, or Jarvis, is doing right now: a reply that states it invented it (tag noact).
    "no_context": [
        ("How's it going?", ("noact",)),
        ("What are you up to?", ("noact",)),
        ("Good to hear.", ("noact", "short", "ack")),
        ("Quiet evening, then.", ("noact", "short")),
    ],
    "check_in": [
        ("Hey.", ("noact", "short")),
        ("You doing all right?", ("noact",)),
        ("What's going on?", ("noact",)),
        ("Anything on your mind?", ("noact",)),
    ],
    # Realistic small talk (use with --real-seed): greetings, polite declines, tired/venting lines. Each is its own chat.
    "greet_a": [("Hey Jarvis.", ("noact", "short")), ("Good evening.", ("noact", "short"))],
    "greet_b": [("Morning.", ("noact", "short")), ("How are you today?", ("noact",))],
    "greet_c": [("Hi there, anything new?", ("noact",)), ("Cool, thanks.", ("noact", "short", "ack"))],
    "decline_a": [("Hey Jarvis.", ("noact", "short")), ("No, that's all right.", ("noact", "short", "ack"))],
    "decline_b": [("Can you hear me okay?", ("noact",)), ("No thanks, I'm good.", ("noact", "short", "ack"))],
    "decline_c": [("Quick check in.", ("noact", "short")), ("Nope, nothing right now.", ("noact", "short", "ack"))],
    "decline_d": [("Hello.", ("noact", "short")), ("No, don't worry about it.", ("noact", "short", "ack"))],
    "decline_e": [("You there?", ("noact", "short")), ("Not at the moment, thanks.", ("noact", "short", "ack"))],
    "vent_a": [("Ugh, I'm so tired today.", ("noact", "short")), ("Yeah, I just want to go to bed.", ("noact", "short"))],
    "vent_b": [("This day has been rough.", ("noact", "short")), ("Everything just piles up.", ("noact", "short"))],
    "vent_c": [("I'm wiped out.", ("noact", "short")), ("Didn't sleep well.", ("noact", "short"))],
    "vent_d": [("Honestly I'm stressed out.", ("noact", "short")), ("Too much going on.", ("noact", "short"))],
    "vent_e": [("I'm so over today.", ("noact", "short")), ("Yeah, thanks for listening.", ("noact", "short", "ack"))],
    # The same kind of line more than once in one chat (tag again): each reply must differ from the ones before it.
    # Tag question: an open question that a bare acknowledgement does not answer.
    "tired_twice": [("Ugh, I'm so tired today.", ("noact", "short", "again")), ("Honestly I'm just beat.", ("noact", "short", "again")),
                    ("I'm exhausted.", ("noact", "short", "again"))],
    "decline_twice": [("Hey Jarvis.", ("noact", "short")), ("No, that's all right.", ("noact", "short", "ack", "again")),
                      ("How is it going?", ("noact", "question")), ("No thanks, I'm good.", ("noact", "short", "ack", "again"))],
    "greet_twice": [("Hey Jarvis.", ("noact", "short", "greet")), ("Hello again.", ("noact", "short", "greet", "again")),
                    ("How is it going?", ("noact", "question")), ("And how's your day been?", ("noact", "question"))],
    # Topic reset (live 2026-11-12): right after a topical answer, a greeting or an unrelated line was answered with
    # more of the old topic. Tag fresh:<words> = the reply must use NONE of these (the topic before); the last line
    # of reset_keep is a real follow-up and must still use the context.
    "reset_report": [("How independent are you now?", ()),
                     ("Hey Jarvis.", ("short", "greet", "fresh:skill|lab|teacher|paid|learn|independen|playbook|pc task|pc work|outside|own two feet|local model|on my own")),
                     ("Good evening.", ("short", "greet", "fresh:skill|lab|teacher|paid|learn|independen|playbook|pc task|pc work|outside|own two feet|local model|on my own"))],
    "reset_workout": [("How should I split a strength workout over three days a week?", ("depth",)),
                      ("Thanks. Did you catch the game last night?", ("fresh:workout|squat|sets|reps|muscle|strength|training|lift|rest day|recover",)),
                      ("Hello again.", ("short", "greet", "fresh:workout|squat|sets|reps|muscle|strength|training|lift|rest day|recover|game|watching"))],
    "reset_sky": [("Why is the sky blue?", ("depth",)),
                  ("Hey Jarvis.", ("short", "greet", "fresh:sky|blue|scatter|wavelength|sunlight|atmosphere|rayleigh")),
                  ("What's your favourite kind of music?", ("fresh:sky|blue|scatter|wavelength|sunlight|atmosphere|rayleigh",))],
    "reset_dns": [("How does DNS actually work?", ("depth",)),
                  ("Morning, Jarvis.", ("short", "greet", "fresh:dns|server|domain|address|lookup|resolver|cache")),
                  ("I think I'll make pancakes today.", ("fresh:dns|server|domain|lookup|resolver|cache",))],
    "reset_keep": [("Why is the sky blue?", ("depth",)),
                   ("Hey Jarvis.", ("short", "greet", "fresh:sky|blue|scatter|wavelength|sunlight|atmosphere|rayleigh")),
                   ("How does a microwave heat food?", ("depth",)),
                   ("And why is that uneven sometimes?", ("recall:microwave|wave|heat|water|spot|turntable|food",))],
    "plan": [
        ("I want to get better at cooking this year. How would you plan that out?", ("depth",)),
        ("What should the first month look like?", ("depth",)),
        ("And how do I stay consistent?", ("depth",)),
    ],
}

# ---------------------------------------------------------------------------------------------- judging a reply
_CANNED = re.compile(
    r"not sure what you meant by|what did you need\?|i caught a short phrase|i didn'?t catch what you meant|"
    r"couldn'?t work out|haven'?t touched anything|still no luck with that one|put it another way|"
    r"local intelligence is unavailable|i haven'?t measured that|okay, i'?ll leave it|i'?m ready to act|"
    r"^(?:here|very good|glad to hear it|standing by|okay), [\w ]+\.$|^yes to what\b|i'?m afraid i can'?t help with that|"
    r"something went wrong|\[eval: ", re.I)
_THIRD = re.compile(r"\bthe (?:owner|user|human)(?:'s)?\b|\b(?:owner|user) (?:is|was|wants|has|said|asked|mentioned)\b", re.I)
_OFFER_TAIL = re.compile(r"(?:let me know|feel free|just say the word|just ask|happy to help|here to help|anything else|"
                         r"how can i (?:help|assist)|what can i do for you|i'?m here if you)[^.?!]*[.?!]?\s*$", re.I)
# The reply ENDS on an offer or a what-next question he did not ask for (written apart from core.speech's own patterns, so
# the measure does not simply mirror the fix).
_OFFER_END = re.compile(
    r"\b(?:want me to|shall i|should i|would you like|do you want|can i (?:get|do|help)|could i|may i|how about i|"
    r"let me know|just say|say the word|feel free|happy to|here if you|here to help|i can (?:always )?\w+[^.?!]* if you|"
    r"if you(?:'d| would)? (?:like|want|need|wish|prefer)|anything\b[^.?!]*\b(?:else\s*\?|you (?:want|need|would like|'d like)|"
    r"i (?:can|could|should))|what (?:can|shall|should|could) i\b|how (?:can|may) i\b|what(?:'s| is) next\b|"
    r"what would you like|where (?:shall|should) (?:i|we) (?:start|begin))", re.I)
_ASKED_FOR_IT = re.compile(r"\bwhat (?:can|could) you (?:do|help)|\bcan you help\b|\bhow can you help\b|\bwhat do you "
                           r"(?:suggest|recommend|offer)\b|\bwhat are (?:my|the) options\b|\bany (?:ideas|suggestions)\b", re.I)
# A statement of what he, or Jarvis, is doing at this moment. On a line that gives no activity it is made up.
_NOW_CLAIM = re.compile(
    r"\bwhile you(?:'re| are)?\s+(?!were\b|said\b)[a-z]+|"
    r"\bkeeping (?:an eye|watch|tabs|track|things|everything|the \w+(?: \w+)? (?:quiet|running|warm|tidy|ticking|humming|"
    r"in order|steady|stable))|"
    r"\b(?:i'?ve been|i have been|i'?m|i am|i was|just|been|busy) (?:quietly |simply |mostly |still |just )?(?:monitoring|"
    r"watching|keeping|tracking|minding|tidying|running|holding|guarding|organi[sz]ing|sorting|reviewing|going through|"
    r"looking after|maintaining|managing|overseeing|checking|indexing|optimi[sz]ing|cataloguing|processing)\b|"
    r"\byou(?:'re| are) (?:still |busy |hard at work |deep in |clearly )*(?:working on|busy with|in the middle of|buried in|"
    r"at work on|wrestling with|focused on|occupied with)|"
    r"\b(?:since|now that|as) you(?:'re| are|'ve been| have been) (?:working|busy|at|resting|taking|catching|done|finished)\b|"
    r"\bgoing on\b[^.?!]*\b(?:session|study|work|project)|\b(?:the )?(?:house|place|system|systems) (?:is|are|hums?|remains?) "
    r"(?:quiet|hum|steady)|\bwhile (?:the )?(?:system|systems|house)\b|\b(?:sitting|waiting|standing) (?:here|by|quietly)\b[^.?!]*\bwhile\b|"
    r"\byou(?:'ve| have) been through\b|\bmeant to do\b|\byou seem to be in a rush\b|\bsimply waiting\b|"
    r"\b(?:the |all )?systems (?:are|remain|have been) (?:quiet|nominal|stable|running|humming|in order)", re.I)
# The synthetic background seeded below: brought into a reply to a line that has nothing to do with it.
_SEEDED = re.compile(r"\bember vale\b|\bworkouts?\b|\brig(?:ging|ged)?\b|\bcertification\b|\bcoffee\b|\btampa\b|"
                     r"\bsister\b|\bunreal\b|\bblender\b|\bthe game\b|\byour game\b|\bshadow world\b|\bhalden\b|\bpantry\b|"
                     r"\bmarathon\b|\bstudy plan\b|\bopen threads?\b|\bdrone\b|\bleg day\b|\bgarmin\b|\bstudy session\b|\bhalf marathon\b|\btheo\b|\bdana\b", re.I)


# The reply ENDS asking what he needs / wants handled / his priority, or promising to handle whatever he needs.
_TASK_PROMPT = re.compile(
    r"\bwhat (?:do|would|did) you (?:need|want)\b|\bwhat(?:'s| is) (?:your|the) (?:priority|plan|first)|"
    r"\byour priorit|\b(?:waiting|ready|standing by|listening) for (?:whatever|your next|you to)|\bready to (?:assist|help|serve)\b|\bat your service\b|\bhandle whatever\b|\bwhatever you (?:need|want|require)\b|\bwhat (?:needs|should) (?:to be )?"
    r"(?:doing|done|handled)|\bwhat(?:'s| is) on (?:the agenda|your list|your plate)|\bhow (?:may|can) i (?:be of|help|assist)|"
    r"\bwhat can i (?:do|get)\b|\bwhere (?:shall|should) we (?:start|begin)", re.I)


def task_prompt_end(said: str, reply: str) -> bool:
    sentences = [x for x in _SENTENCES.split(reply.strip()) if x.strip()]
    if not sentences or _ASKED_FOR_IT.search(said or ""):
        return False
    return bool(_TASK_PROMPT.search(sentences[-1].replace("’", "'")))


def offer_end(said: str, reply: str) -> bool:
    """Does the reply end on an offer or a what-next question he did not ask for?"""
    sentences = [x for x in _SENTENCES.split(reply.strip()) if x.strip()]
    if not sentences or _ASKED_FOR_IT.search(said or ""):
        return False
    return bool(_OFFER_END.search(sentences[-1].replace("’", "'")))


_META = re.compile(r"answer_directly|function call|tool call|tool_call|<think|<background|<retrieved|\{\s*\"", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def quotes_back(said: str, reply: str) -> bool:
    """Five or more consecutive words of his line repeated in the reply (the film-JARVIS rule R8)."""
    his, mine = _words(said), " " + " ".join(_words(reply)) + " "
    return any(" " + " ".join(his[i:i + 5]) + " " in mine for i in range(len(his) - 4))


def judge(said: str, reply: str, tags: tuple) -> dict:
    spoken = "" if reply.strip().startswith("[ignore]") else reply.strip()
    tool_names = [n for n in registry.names() if "_" in n and n in spoken]
    sentences = [s for s in _SENTENCES.split(spoken) if s.strip()]
    recall = [t.split(":", 1)[1] for t in tags if t.startswith("recall:")]
    fresh = [t.split(":", 1)[1] for t in tags if t.startswith("fresh:")]
    return {
        "fresh_needed": bool(fresh),
        "old_topic": bool(fresh) and any(re.search(rf"(?:{f})", spoken, re.I) for f in fresh),
        "words": len(spoken.split()),
        "sentences": len(sentences),
        "silent": not spoken,
        "canned": bool(spoken) and bool(_CANNED.search(spoken)),
        "quote_back": bool(spoken) and quotes_back(said, spoken),
        "third_person": bool(_THIRD.search(spoken)),
        "offer": bool(spoken) and (intelligence.generic_offer(spoken) or bool(_OFFER_TAIL.search(spoken))),
        "meta": bool(_META.search(spoken)) or bool(tool_names),
        "offer_end": bool(spoken) and offer_end(said, spoken),
        "task_prompt": bool(spoken) and task_prompt_end(said, spoken),
        "invented_now": "noact" in tags and bool(_NOW_CLAIM.search(spoken.replace("\u2019", "'"))),
        "dragged_background": bool(_SEEDED.search(spoken)) and not _SEEDED.search(said),
        "wrong_time": bool(spoken) and chatturn.wrong_greeting(spoken, said, CLOCK[0]),
        "unanswered": bool(spoken) and chatturn.open_question(said) and chatturn.only_acknowledges(spoken),
        "recall_needed": bool(recall),
        "recall_ok": all(re.search(rf"\b(?:{r})", spoken, re.I) for r in recall) if recall else None,
    }


# ---------------------------------------------------------------------------------------------- running
def new_brain() -> Brain:
    b = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
    b.context = memory.context_block
    return b


def seed() -> None:
    """A little synthetic memory, so 'what he knows about him' has something in it (the sandbox starts empty)."""
    for fact, category in (("Prefers his coffee strong and black.", "preference"),
                           ("Is studying for a networking certification in the evenings.", "goal"),
                           ("Has a younger sister who lives in Tampa.", "family"),
                           ("Is building a hobby game called Ember Vale in Unreal Engine.", "goal"),
                           ("Is rigging a robot character in Blender for the game.", "goal"),
                           ("Follows a strength workout plan three days a week.", "preference")):
        try:
            memory.remember(fact, category, False)
        except Exception as error:  # noqa: BLE001  (the eval still runs without seeds)
            print(f"[chat-eval] seed skipped: {type(error).__name__}")


def seed_real() -> None:
    """--real-seed: background shaped like the owner's real data (all synthetic): several ongoing named projects, a workout
    plan, family, a household fact, journal lines with 'Still open' threads, lessons. The plain seed() is too thin: the
    live defects (stating what he is doing now from a project fact, ending on a task prompt) did not show with it."""
    import datetime as _dt
    from core import journal
    seed()
    for fact, category in (("My wife's name is Dana and she works as a nurse.", "family"),
                           ("Has a son named Theo who is six.", "family"),
                           ("Works as a freelance 3D artist for a small studio.", "work"),
                           ("Is building a game called Halden Reach in Unreal Engine 5, a long-running project.", "goal"),
                           ("Is also rebuilding the pantry automation scripts, an ongoing project.", "goal"),
                           ("Is training for a half marathon, running four mornings a week.", "goal"),
                           ("Follows a push-pull-legs workout plan and skips leg day when tired.", "preference"),
                           ("Is studying a networking certification in the evenings.", "goal"),
                           ("Prefers short answers and no offers at the end of replies.", "preference")):
        try:
            memory.remember(fact, category, False)
        except Exception as error:  # noqa: BLE001
            print(f"[chat-eval] real seed skipped: {type(error).__name__}")
    memory.remember("The household keeps a shared grocery list on the fridge door.", "other", True)
    now = _dt.datetime.now()
    rows = [
        {"id": "j1", "t": (now - _dt.timedelta(days=3)).isoformat(timespec="minutes"),
         "summary": "Worked through the Halden Reach character rig and the pantry scripts; he said the week was packed and "
                    "he was planning leg day for Saturday.",
         "open": ["finish the Halden Reach rig weights", "pantry script scheduling", "pick a half marathon date"]},
        {"id": "j2", "t": (now - _dt.timedelta(days=1)).isoformat(timespec="minutes"),
         "summary": "Talked about being tired after a long shift week; discussed Theo's birthday plans and the "
                    "certification study schedule.",
         "open": ["decide Theo's party date", "study plan for the networking exam", "Halden Reach level one blockout"]},
    ]
    storage.save("journal-pc.json", rows)
    for lesson in ("Do not recite his projects when he only says hello.",
                   "When he is tired, keep it plain and short; no questions."):
        journal.add_lesson(lesson, "owner")


CLOCK = [None]         # --clock: the pinned local time (None: the real clock)
PHONE = False          # --phone: each line goes in as core.appserver's /api/chat sends it
_PHONE_NOTE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "


def turn(brain: Brain, said: str, tags: tuple, earlier=()) -> dict:
    model_calls.clear()
    acted.clear()
    tools_run.clear()
    spoken: list[str] = []
    first: list[float] = []
    started = time.monotonic()

    def hear(sentence: str) -> None:
        if not first and sentence.strip():
            first.append(time.monotonic() - started)
        spoken.append(sentence)

    brain.ask([{"type": "text", "text": _PHONE_NOTE + said}] if PHONE else said, hear)
    took = time.monotonic() - started
    reply = " ".join(s.strip() for s in spoken if s.strip())
    replies = [c for c in model_calls if c["kind"] == "reply"]
    row = {"you": said, "jarvis": reply, "tags": list(tags), "seconds": round(took, 2),
           "first_words_s": round(first[0], 2) if first else None,
           "calls": len(model_calls), "reply_calls": len(replies), "classifier_calls": len(model_calls) - len(replies),
           "prompt_tokens": max((c["prompt_tokens"] for c in replies), default=0),
           "sent_tokens_est": max((c["sent_tokens_est"] for c in replies), default=0),
           "completion_tokens": sum(c["completion_tokens"] for c in replies),
           "before_model_s": round(model_calls[0]["at"] - started, 2) if model_calls else None,
           "call_list": [f"{c['kind']}{':' + c['profile'] if c['profile'] else ''} {c.get('seconds', 0)}s/{c['prompt_tokens']}t"
                         for c in model_calls],
           "pc_action": list(acted), "tools": list(tools_run)}
    row.update(judge(said, reply, tags))
    row["repeated"] = bool(reply.strip()) and any(chatturn.same_reply(reply, e) for e in list(earlier)[-chatturn.RECENT_REPLIES:])
    flags = [k for k in FLAGS if row[k]]
    if row["pc_action"]:
        flags.append("PC-ACTION")
    if row["recall_needed"] and not row["recall_ok"]:
        flags.append("forgot")
    print(f"\nYOU: {said}\n  JARVIS ({took:.1f}s, first {row['first_words_s']}s, calls {row['calls']}, "
          f"prompt {row['prompt_tokens']}, out {row['completion_tokens']}; {'; '.join(row['call_list'])}): {reply[:600]}"
          + (f"\n  !! {', '.join(flags)}" if flags else ""))
    return row


FLAGS = ("silent", "canned", "quote_back", "third_person", "offer", "meta", "offer_end", "task_prompt", "invented_now",
         "dragged_background", "repeated", "wrong_time", "unanswered", "old_topic")


def _median(values) -> float:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else 0.0


def _share(rows, key) -> str:
    n = sum(1 for r in rows if r[key])
    return f"{n}/{len(rows)} ({100 * n / max(len(rows), 1):.0f}%)"


def summarize(rows: list[dict]) -> dict:
    said = [r for r in rows if not r["silent"]]
    depth = [r for r in rows if "depth" in r["tags"]]
    short = [r for r in rows if "short" in r["tags"]]
    acks = [r for r in rows if "ack" in r["tags"]]
    recalls = [r for r in rows if r["recall_needed"]]
    turn5 = [r for r in rows if "turn5" in r["tags"]]
    noact = [r for r in rows if "noact" in r["tags"]]
    opening = [r for r in rows if r.get("_first")]
    return {
        "turns": len(rows),
        "median reply words": _median(r["words"] for r in said),
        "median reply words, depth turns": _median(r["words"] for r in depth),
        "median sentences, depth turns": _median(r["sentences"] for r in depth),
        "depth turns with 4+ sentences": _share(depth, "_deep") if depth else "0/0",
        "median reply words, acknowledgement turns": _median(r["words"] for r in acks),
        "quote-back or canned line": _share(rows, "_canned_or_quote"),
        "short lines answered with a quote-back, canned line or silence": _share(short, "_short_fault") if short else "0/0",
        "third-person slips": _share(rows, "third_person"),
        "machinery said aloud (tool names, JSON, tags)": _share(rows, "meta"),
        "replies ending in a generic offer": _share(rows, "offer"),
        "replies ending in an unrequested offer or what-next question": _share(rows, "offer_end"),
        "replies ending in a task prompt (what do you need / your priority / handle whatever)": _share(rows, "task_prompt"),
        "invented present activity (turns that give none)": _share(noact, "invented_now") if noact else "0/0",
        "background brought into a line it does not bear on": _share(rows, "dragged_background"),
        "greeting or new subject answered with the topic before": (
            f"{sum(1 for r in rows if r['old_topic'])}/{sum(1 for r in rows if r['fresh_needed'])}"),
        "reply repeats an earlier reply of the same conversation": _share(rows, "repeated"),
        "same reply given in more than one conversation": _share(rows, "_stock"),
        "greeting with the wrong time of day": _share(rows, "wrong_time"),
        "open question answered with a bare acknowledgement": _share(rows, "unanswered"),
        "no reply at all": _share(rows, "silent"),
        "lines that became a PC action or a skill call": _share(rows, "_acted"),
        "earlier fact used when needed": (f"{sum(1 for r in recalls if r['recall_ok'])}/{len(recalls)}" if recalls else "0/0"),
        "turn-5 fact used": ("yes" if turn5 and all(r["recall_ok"] for r in turn5) else "no") if turn5 else "not run",
        "median model calls per turn": _median(r["calls"] for r in rows),
        "turns with a JSON classifier call": _share(rows, "_classified"),
        "median prompt tokens (reply call)": _median(r["prompt_tokens"] for r in rows if r["reply_calls"]),
        "median completion tokens": _median(r["completion_tokens"] for r in rows if r["reply_calls"]),
        "median seconds to first words": _median(r["first_words_s"] for r in rows),
        "p90 seconds to first words": _p90([r["first_words_s"] for r in rows]),
        "median seconds to first words, depth turns": _median(r["first_words_s"] for r in depth),
        "median seconds per turn": _median(r["seconds"] for r in rows),
        "seconds to first words, first turn of the run": rows[0]["first_words_s"] if rows else None,
        "median seconds to first words, first turn of a conversation": _median(r["first_words_s"] for r in opening),
        "median seconds to first words, later turns": _median(r["first_words_s"] for r in rows if not r.get("_first")),
        "median seconds before the first model call": _median(r.get("before_model_s") for r in rows),
        "turns with a model call before the reply call": _share(rows, "_pre_call"),
    }


def _p90(values) -> float:
    values = sorted(v for v in values if v is not None)
    return round(values[min(len(values) - 1, int(0.9 * len(values)))], 2) if values else 0.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", default="run")
    parser.add_argument("--only", nargs="*", default=[], help="conversation names (default: all)")
    parser.add_argument("--real-seed", action="store_true", help="seed the sandbox with background shaped like real data")
    parser.add_argument("--clock", default="", help="pin the local time, e.g. 2026-11-12T08:10 (default: the real clock)")
    parser.add_argument("--phone", action="store_true", help="send each line as the phone app's /api/chat does")
    args = parser.parse_args(argv)
    global PHONE
    PHONE = args.phone
    if args.clock:
        import datetime as _dt
        CLOCK[0] = _dt.datetime.fromisoformat(args.clock)
        chatturn._now = lambda: CLOCK[0]
    picked = [n for n in CONVERSATIONS if not args.only or n in args.only]
    seed_real() if args.real_seed else seed()
    rows, by_chat = [], {}
    for name in picked:
        print(f"\n===== {name} =====")
        brain = new_brain()
        by_chat[name] = []
        for said, tags in CONVERSATIONS[name]:
            by_chat[name].append(turn(brain, said, tags, [r["jarvis"] for r in by_chat[name]]))
        if by_chat[name]:
            by_chat[name][0]["_first"] = True
        rows += by_chat[name]
    seen: dict = {}
    for name in picked:
        for text in {chatturn._flat(r["jarvis"]) for r in by_chat[name] if r["jarvis"].strip()}:
            seen[text] = seen.get(text, 0) + 1
    for r in rows:
        r["_stock"] = seen.get(chatturn._flat(r["jarvis"]), 0) > 1
        r["_deep"] = r["sentences"] >= 4
        r["_canned_or_quote"] = r["canned"] or r["quote_back"]
        r["_short_fault"] = r["canned"] or r["quote_back"] or r["silent"]
        r["_acted"] = bool(r["pc_action"])
        r["_classified"] = r["classifier_calls"] > 0
        r["_pre_call"] = r["calls"] > 1
    summary = summarize(rows)
    out = ROOT / "runs"
    out.mkdir(exist_ok=True)
    lines = [f"# Chat eval: {args.label}", "",
             f"Model {intelligence.model('fast')}, gateway {gateway.mode()}, {len(picked)} conversations, {len(rows)} turns, "
             f"{time.strftime('%Y-%m-%d %H:%M')}{', clock pinned at ' + args.clock if args.clock else ''}. Synthetic lines; sandboxed data; no PC action can run.", "",
             "| Measure | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary.items()]
    for name in picked:
        lines += ["", f"## {name}", ""]
        for r in by_chat[name]:
            flags = [k for k in FLAGS if r[k]]
            flags += ["PC-ACTION: " + "; ".join(r["pc_action"])] if r["pc_action"] else []
            flags += ["forgot the earlier fact"] if r["recall_needed"] and not r["recall_ok"] else []
            lines += [f"- **You:** {r['you']}",
                      f"  **Jarvis:** {r['jarvis'] or '(nothing)'}",
                      f"  _{r['words']} words, {r['sentences']} sentences; first words {r['first_words_s']} s, whole turn "
                      f"{r['seconds']} s; calls {r['calls']} ({r['classifier_calls']} classifier: {'; '.join(r['call_list'])}); prompt "
                      f"{r['prompt_tokens']} tokens, completion {r['completion_tokens']}"
                      + (f"; tags {', '.join(r['tags'])}" if r["tags"] else "") + "_"
                      + (f"  **Flags: {', '.join(flags)}**" if flags else "")]
    (out / f"chat-eval-{args.label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / f"chat-eval-{args.label}.json").write_text(
        json.dumps({"label": args.label, "summary": summary, "conversations": by_chat}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    print("\n" + "\n".join(f"{k}: {v}" for k, v in summary.items()))
    print(f"\nWrote {out / f'chat-eval-{args.label}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
