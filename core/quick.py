"""Instant, exact answers for everyday commands, before any model runs.

"Set a 3 minute timer for tea", "cancel the tea timer", "what timers are running", "start focus mode",
"how was my week", "what's my streak", "how many days until Christmas", "what time is it". The small local model
sometimes skipped the tool or contradicted its result; these have one right answer, so they never reach it.
Returns None for anything else (the brain takes over).
"""
import re
from datetime import datetime

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "forty-five": 45, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
_UNIT = r"(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)"
_DUR = re.compile(r"(\d+(?:\.\d+)?|forty[- ]five|" + "|".join(sorted(_NUM, key=len, reverse=True)) + r")\s*-?\s*" + _UNIT + r"\b", re.I)
_HALF_HOUR = re.compile(r"\bhalf an? hour\b", re.I)


def duration(text: str) -> int:
    """'3 minute', 'an hour and 20 minutes', '90 seconds', 'half an hour' -> seconds (0 if none)."""
    total = 1800 if _HALF_HOUR.search(text) else 0
    for n, unit in _DUR.findall(_HALF_HOUR.sub(" ", text)):
        value = float(n) if n[0].isdigit() else _NUM.get(n.lower().replace("-", " "), _NUM.get(n.lower(), 0))
        u = unit.lower()
        total += value * (3600 if u.startswith("h") else 60 if u.startswith("m") else 1)
    return int(total)


_FILLER = {"a", "an", "the", "my", "set", "start", "timer", "for", "please", "jarvis", "hey", "me", "up", "put", "on", "make",
           "minute", "minutes", "min", "mins", "second", "seconds", "sec", "secs", "hour", "hours", "hr", "hrs", "and", "can",
           "you", "could", "would", "of", "to", "in", "just", "quick", "new", "another"}


def _label(text: str) -> str:
    t = _DUR.sub(" ", _HALF_HOUR.sub(" ", text.lower()))
    m = re.search(r"\btimer\s+for\s+(?:the\s+|my\s+|a\s+)?([a-z][a-z' ]{0,30})", t)
    if m:
        words = [w for w in m.group(1).split() if w not in _FILLER]
        if words:
            return " ".join(words[:3])
    m = re.search(r"\b([a-z][a-z']{1,20}(?:\s+[a-z][a-z']{1,20})?)\s+timer\b", t)
    if m:
        words = [w for w in m.group(1).split() if w not in _FILLER]
        if words:
            return " ".join(words[-2:])
    return "timer"


_TIMER_SET = re.compile(r"\b(?:set|start|put on|make|give me)\b[^.?!]*\btimer\b|\btimer for\b|^\s*(?:a\s+)?\S+\s*-?\s*(?:minute|min|second|sec|hour)s?\s+timer\b", re.I)
_TIMER_CANCEL = re.compile(r"\b(?:cancel|stop|kill|delete|clear|turn off|remove)\b([^.?!]*)\btimers?\b", re.I)
_TIMER_LIST = re.compile(r"\b(?:what|which|any|how many|list|show)\b[^.?!]*\btimers?\b|\bhow (?:long|much time)\b[^.?!]*\b(?:left|remaining|on)\b[^.?!]*\btimer\b", re.I)
_FOCUS_START = re.compile(r"\b(?:start|begin|turn on|enable|let'?s do|do)\b[^.?!]*\b(?:focus mode|focus session|focus block|pomodoro)\b|^\s*(?:focus mode|pomodoro)\s*[.!]?\s*$", re.I)
_FOCUS_STOP = re.compile(r"\b(?:stop|end|turn off|cancel|disable|quit)\b[^.?!]*\b(?:focus mode|focus session|pomodoro)\b", re.I)
_FOCUS_STATUS = re.compile(r"\bhow (?:long|much time)\b[^.?!]*\b(?:focus|pomodoro|break)\b", re.I)
_WEEK = re.compile(r"\bhow (?:was|is|'s|has) my week\b|\bhow did my week go\b|\bweekly (?:report|recap|summary)\b|\bweek in review\b|\brecap (?:of )?my week\b|\bmy week so far\b", re.I)
_ACHIEVE = re.compile(r"\bmy (?:training |workout |gym |fitness )?(?:streak|achievements?|badges?)\b|\bwhat badges\b|\bhow (?:long|big) is my streak\b", re.I)
_DAYS = re.compile(r"\bhow (?:many|much) (?:days|time|weeks)\b(?: (?:is it|are there|left|do i have))? (?:until|till|til|to|before|left (?:until|till|before))\s+(.+)", re.I)
_COUNTDOWNS = re.compile(r"\b(?:my|list|show|what are my|any) countdowns?\b|\bwhat am i counting down to\b", re.I)

# The volume skill accepts an absolute target (0-100); a local model interpreted
# "lower it by 50%" as a target of -50, which the skill treats as its read-only
# sentinel.  These explicit spoken forms therefore need deterministic arithmetic
# before a model can translate a relative change into the wrong contract.
_VOLUME_DELTA = re.compile(
    r"\b(?:(?P<move>turn|put|make)\s+(?:the\s+)?volume\s+(?P<direction>up|down|higher|lower|louder|quieter)"
    r"|(?P<verb>raise|increase|lower|decrease)\s+(?:the\s+)?volume)"
    r"(?:\s+by)?\s+(?P<amount>\d{1,3})\s*(?:%|percent\b)", re.I)
_VOLUME_SET = re.compile(r"\b(?:set|put|make)\s+(?:the\s+)?volume\s+(?:to|at)\s*(?P<amount>\d{1,3})\s*(?:%|percent\b)", re.I)
_VOLUME_READ = re.compile(r"\b(?:what(?:'s| is) (?:the )?volume|(?:check|show) (?:me )?(?:the )?volume)\b", re.I)


# The whole question, nothing more: "what time is the game?" still goes to the brain.
_CLOCK = re.compile(r"(?:what(?:'s| is) the time|what time is it|do you (?:have|know) the time|got the time)"
                    r"(?: (?:now|right now|please|sir))*", re.I)
_DATE = re.compile(r"(?:what(?:'s| is) (?:the |today'?s )?date|what day is (?:it|today)|what(?:'s| is) today)"
                   r"(?: today)?(?: (?:now|please|sir))*", re.I)


# "What's 15 percent of 80?": the local model called engineering_calc('0.15 * 80', convert_to='percent') and said
# "1200 percent". One right answer, so it never reaches a model. Only this shape; "15% of my budget" is the brain's.
_PERCENT_OF = re.compile(r"(?:(?:so |ok |okay |hey )?(?:what(?:'s| is|s)|how much is|calculate|tell me) )?"
                         r"(\d+(?:\.\d+)?) ?(?:%|percent|per cent) (?:of|off(?: of)?) (?:\$|usd )?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
                         r"(?: (?:dollars|bucks|please|sir))*", re.I)


# "What local AI are you using?" got "the PC's built-in AI and some custom scripts" (invented) and, another time,
# "Local intelligence is unavailable". The answer is in the configuration, not in a model.
_CAPABILITIES = re.compile(r"(?:so |okay |ok |and )?(?:what|which)(?: kind of| sort of)? (?:(?:pc|computer)(?: control)? )?(?:skills|abilities|things)"
                           r"(?: (?:do|did|can) you (?:have|know|do))?(?: (?:for|on|with) (?:my |the )?(?:pc|computer)(?: control)?)?"
                           r"|what (?:have|did) you (?:master|mastered|learn|learned)(?: (?:for|on|in|about) (?:my |the )?(?:pc|computer)(?: control)?)?(?: so far| yet)?"
                           r"|what can you do (?:on|with|to) (?:my|the|this) (?:pc|computer)|what are your (?:pc |computer )?(?:skills|abilities)", re.I)
# "What's using the most memory", "the most demanding memory", "memory hogs" (owner 2026-11-07: the gateway answered the
# total RAM, or opened File Explorer and read the screen out).
_HOGS = re.compile(r"\b(?:most|biggest|top|highest|heaviest|most demanding)\b[^.?!]{0,40}\b(?:memory|ram)\b"
                   r"|\b(?:using|eating|hogging|taking(?: up)?|uses|takes)\b[^.?!]{0,25}\b(?:the )?most\b[^.?!]{0,15}\b(?:memory|ram)\b"
                   r"|\bmemory hogs?\b|\bram hogs?\b"
                   # "Scan my PC and look for things that are wasting my memory." (live 2026-11-15: answered the total, twice)
                   r"|\b(?:things?|what(?:'s| is)?|apps?|programs?|stuff)\b[^.?!]{0,20}\b(?:wasting|eating up|hogging|clogging(?: up)?)"
                   r"\s+(?:all\s+(?:of\s+)?)?(?:my\s+|the\s+)?(?:memory|ram)\b", re.I)
_PROC_NAMES = {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "claude": "Claude", "chatgpt": "ChatGPT",
               "steamwebhelper": "Steam", "steam": "Steam", "explorer": "File Explorer", "vmmem": "WSL / virtual machines",
               "vmmemwsl": "WSL", "code": "VS Code", "discord": "Discord", "ollama": "Ollama (local AI)", "ollama_llama_server": "Jarvis's local AI model", "llama-server": "Jarvis's local AI model",
               "pythonw": "Jarvis", "python": "Python", "unrealeditor": "Unreal Editor", "blender": "Blender", "r5apex": "Apex Legends",
               "msmpeng": "Windows Defender", "dwm": "Windows desktop"}
_SKIP_PROCS = {"system idle process", "system", "registry", "memory compression", "secure system"}


# "What's taking all my CPU right now? My CPU and my RAM. What's running on there?" got "I don't have a live list of
# what's hogging your CPU or RAM right now" from the chat path (owner 2026-11-14). It is a reading, so it is taken here.
_BUSY = re.compile(r"\bwhat(?:'s| is| are|s)? (?:taking|using|eating|hogging|maxing)(?: up| out)? (?:all (?:of )?|most of |so much (?:of )?)?"
                   r"(?:my |the |this )?(?:cpu|processor|ram|memory)"
                   r"(?: and (?:my |the )?(?:cpu|processor|ram|memory))?(?: right now| now| at the moment| on (?:my|the|this) (?:pc|computer))?", re.I)
# What he may add in the same breath ("My CPU and my RAM. What's running on there?"): more of the same question only.
_BUSY_MORE = re.compile(r"(?:my |the )?(?:cpu|processor|ram|memory)(?: and (?:my |the )?(?:cpu|processor|ram|memory))?"
                        r"|what(?:'s| is|s) running(?: on (?:there|it|here|(?:my|the|this) (?:pc|computer)))?(?: right now| now)?", re.I)


def _asks_whats_busy(bare: str) -> bool:
    """Every sentence of the line is that question: "what's using my ram, close it" and "what is taking my memory in the
    contract" are not (an order, another subject), so they go on to the routes that act or think (review 2026-11-14)."""
    parts = [p.strip(" ?.!,") for p in re.split(r"[.!?]+\s+", (bare or "").strip())]
    parts = [re.sub(r"^(?:(?:so|and|okay|ok|hey|well|jarvis),? )+", "", p, flags=re.I) for p in parts if p]
    parts = [re.sub(r"(?:,? (?:sir|please|jarvis|thanks|thank you))+$", "", p, flags=re.I) for p in parts]
    parts = [p for p in parts if p and not re.fullmatch(r"sir|please|jarvis|thanks|thank you", p, re.I)]
    return (bool(parts) and any(_BUSY.fullmatch(p) for p in parts)
            and all(_BUSY.fullmatch(p) or _BUSY_MORE.fullmatch(p) for p in parts))


def whats_busy(top: int = 3, pause: float = 0.4) -> str:
    """Who is using the processor and the memory right now, by app, in two sentences. Two whole-table snapshots a
    moment apart (core/procsnap): about half a second, where asking each process took 11 on this PC."""
    import os
    import time
    from core import procsnap
    try:
        before = {pid: cpu for pid, _, _, cpu in procsnap.timed()}
        started = time.monotonic()
        time.sleep(pause)
        rows = procsnap.timed()
        span = max(time.monotonic() - started, 0.05)
    except Exception:  # noqa: BLE001
        return "I couldn't read what is running just now."
    work, mem = {}, {}
    for pid, name, size, cpu in rows:
        name = name.rsplit(".", 1)[0]
        if not name or name.casefold() in _SKIP_PROCS:
            continue
        label = _PROC_NAMES.get(name.casefold(), name)
        mem[label] = mem.get(label, 0) + size
        if pid in before and cpu >= before[pid]:
            work[label] = work.get(label, 0) + (cpu - before[pid])
    if not mem:
        return "I couldn't read what is running just now."
    cores = os.cpu_count() or 1
    share = {n: used / (span * 1e7 * cores) * 100 for n, used in work.items()}
    busy = [(n, v) for n, v in sorted(share.items(), key=lambda kv: -kv[1])[:top] if v >= 1]
    total = min(100.0, sum(share.values()))
    said = f"The processor is at about {total:.0f} percent"
    said += (": " + ", ".join(f"{n} {v:.0f}" for n, v in busy) + ".") if busy else ", with nothing standing out."
    heavy = ", ".join(f"{n} {b / 2**30:.1f} GB" for n, b in sorted(mem.items(), key=lambda kv: -kv[1])[:top])
    try:
        import psutil
        vm = psutil.virtual_memory()
        return f"{said} Memory is {vm.percent:.0f} percent of {vm.total / 2**30:.0f} GB in use: {heavy}."
    except Exception:  # noqa: BLE001
        return f"{said} Using the most memory: {heavy}."


def memory_hogs(top: int = 3) -> str:
    """The apps using the most memory right now, by app (an app's processes added together), in one sentence."""
    import psutil
    used: dict = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (p.info["name"] or "").rsplit(".", 1)[0]
            if not name or name.casefold() in _SKIP_PROCS or not p.info["memory_info"]:
                continue
            label = _PROC_NAMES.get(name.casefold(), name)
            used[label] = used.get(label, 0) + p.info["memory_info"].rss
        except Exception:  # noqa: BLE001
            continue
    best = sorted(used.items(), key=lambda kv: -kv[1])[:top]
    mem = psutil.virtual_memory()
    if not best:
        return "I couldn't read what is using memory right now."
    apps = ", ".join(f"{n} {b / 2**30:.1f} GB" for n, b in best)
    return f"Using the most memory: {apps}. {mem.percent:.0f}% of {mem.total / 2**30:.0f} GB is in use."


_WHICH_MODEL = re.compile(r"(?:what|which|what's the) (?:local )?(?:ai|a\.i\.|model|models|llm|brain)s?(?: model)?"
                          r"(?: are you| am i| is this| do you| do i| is jarvis)? ?(?:using|running on|running|on|use|run on|run|powered by)?"
                          r"(?: right now| now| locally| on the pc| at the moment| for me)?", re.I)


# "Jarvis, are you using a paid model to do this or is that all local?" (2026-11-12: "reading your screen"; sandbox 2026-11-21:
# "I run on a paid cloud model that costs extra credits"). Any question whether HE runs paid, in the cloud or locally is
# answered from the settings (the sentence above), never by the model's guess. Credits and usage keep quick._credits.
_OWN_BRAIN = re.compile(r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|wait|um|uh|but)\W+)*(?:are|is|do|does|did)\s+(?:you|jarvis)\s+"
                        r"(?=[^?]*\b(?:paid|cloud|local(?:ly)?|api|credits?)\b)(?:(?:all|really|actually|still|just|now)\s+)?(?:an?\s+)?"
                        r"(?:using|use|running|run|thinking|on|paid|local|in the cloud)\b[^?]{0,110}$", re.I)


def _which_model() -> str:
    from core import intelligence
    if not intelligence.enabled():
        return "I'm thinking with the cloud model right now, sir; the local models are switched off."
    fast, advanced = intelligence.model("fast"), intelligence.model("advanced")
    vision = intelligence.vision_model()
    # One plain sentence (live 2026-11-21 the answer was "Locally I think with Qwen 3.5 9B for quick replies, and it sees pictures too; ...")
    if intelligence.local_only():
        tail = ", locally on this PC; nothing leaves the house."
    elif intelligence.hard_first():
        tail = (", locally on this PC; harder questions and PC jobs it can't plan go to Claude on your API credit, and I "
                "practise what Claude solved so I can do it myself next time.")
    else:
        tail = ", locally on this PC; the cloud only steps in when it fails."
    others = [m for m in dict.fromkeys((advanced, vision)) if m and m != fast]
    if not others:
        return f"I run on {_say_model(fast)}" + tail
    roles = [f"{_say_model(advanced)} for harder questions"] if advanced != fast else []
    if vision and vision not in (fast, advanced):
        roles.append(f"{_say_model(vision)} for pictures")
    return f"I run on {_say_model(fast)}, with " + " and ".join(roles) + tail


FROM_THE_PHONE = "From the phone, I think with the AI on your home PC."


def _which_model_here(raw: str) -> str | None:
    """Which AI answers HIM. On the laptop (the phone's server) every model call is relayed to the PC's brain, so the PC
    says what it runs: the laptop's own models never answer a turn. Live 2026-11-10 the phone heard 'Llama 3.2 3B for
    quick replies, Gemma 4 12b It Qat Q 4_0 for harder ones' (the laptop's .env) and then, for the next question,
    'Qwen 3.5 9B is entirely on your graphics card' (the PC, measured)."""
    from core import pcbridge
    if pcbridge.local_pc():
        return _which_model()
    try:
        pcbridge.require_owner()            # PC questions from the phone are the owner's (core.pcfast's rule)
    except pcbridge.PCError:
        return FROM_THE_PHONE
    from core import pcfast
    return pcfast.ask_pc(raw) or FROM_THE_PHONE


# "Is it running on my graphics card?" right after he asked which AI (live baseline 2026-11-10): the brain said "it runs
# on your CPU, not your graphics card" while Ollama had all 6.6 GB of qwen3.5:9b in video memory. Measured, never guessed.
_WHERE_AI = re.compile(r"(?:is|are|does|do) (?:it|that|you|the (?:local )?(?:ai|a\.i\.|model|brain|llm)|your (?:local )?"
                       r"(?:ai|brain|model)|qwen(?: [\w.]+)*?|ollama)(?: (?:running|run|runs|sitting|loaded|using|use|working))?"
                       r"(?: (?:on|in|from|off))?(?: (?:my|the|your))? (?:gpu|graphics card|graphics|video card|cpu|processor|vram|"
                       r"video memory)(?: or (?:on )?(?:the |my )?(?:gpu|graphics card|cpu|processor))?(?: right now| now)?", re.I)


def _where_ai() -> str | None:
    """Where the local brain runs right now, from Ollama's own report (size vs size_vram). Only on the PC that runs it:
    the laptop relays its thinking to the PC, so its own Ollama says nothing about the brain he talks to."""
    from core import intelligence, pcbridge, warmth
    if not pcbridge.local_pc() or not intelligence.enabled():
        return None
    names = warmth.jarvis_models()
    if not names:
        return None                                   # not Ollama (LM Studio): nothing here to measure
    loaded = warmth.loaded()
    if loaded is None:
        return "I couldn't ask Ollama where it's running just now, sir, so I won't guess."
    parts = []
    for name in names:
        said = _say_model(name)
        size, vram = loaded.get(name, (0, 0))
        if not size:
            parts.append(f"{said} isn't loaded at the moment, so it isn't using the graphics card or the processor")
        elif vram >= size:
            parts.append(f"{said} is entirely on your graphics card: all {size / 1e9:.1f} GB of it is in video memory")
        elif vram > 0:
            parts.append(f"{said} is only partly on your graphics card: {vram / 1e9:.1f} of its {size / 1e9:.1f} GB is in "
                         f"video memory, and the rest runs on the processor, which is slower")
        else:
            parts.append(f"{said} is running on the processor from system memory, not on the graphics card")
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] + ", sir."


def _say_model(name: str) -> str:
    """'llama3.2:3b' -> 'Llama 3.2 3B' (said aloud)."""
    base, _, tag = name.partition(":")
    base = re.sub(r"(?<=[a-z])(?=\d)", " ", base).replace("-", " ")
    base = " ".join(w.capitalize() if w.isalpha() else w for w in base.split())
    size = re.search(r"\d+(?:\.\d+)?b", tag)
    return base + (f" {size.group(0).upper()}" if size else "")


def _number(value: float) -> str:
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _volume_command(text: str) -> str | None:
    """Execute an unambiguous local volume request and report its actual result.

    Returning None deliberately leaves less specific media language to the
    normal planner.  No model inference is necessary for a percentage change.
    """
    delta, absolute = _VOLUME_DELTA.search(text), _VOLUME_SET.search(text)
    if not (delta or absolute or _VOLUME_READ.search(text)):
        return None
    from tools import media

    before = media.state()
    current = before.get("volume")
    if not isinstance(current, int):
        return None
    if delta:
        amount = int(delta.group("amount"))
        direction = (delta.group("direction") or delta.group("verb") or "").casefold()
        target = current + amount if direction in {"up", "higher", "louder", "raise", "increase"} else current - amount
    elif absolute:
        target = int(absolute.group("amount"))
    else:
        return f"Volume is {current}%{' (muted)' if before.get('muted') else ''}."
    target = max(0, min(target, 100))
    media.set_volume(target)
    after = media.state()
    actual = after.get("volume")
    if not isinstance(actual, int):
        return None
    return f"Volume is {actual}%{' (muted)' if after.get('muted') else ''}."


# "From now on, only speak English with me.": qwen3.5 answered "Understood, sir." without calling the skill (convo_local
# intent, 2026-11-10; it passed on llama3.2). A request this plain runs the real skill through the registry.
_OHMS = re.compile(r"(?P<v>\d+(?:\.\d+)?)\s*(?P<vk>k(?:ilo)?)?\s*(?:v|volts?)\b[^\d]{0,40}?\b(?:across|through|over|on|with|into)\s+(?:a\s+)?"
                   r"(?P<r>\d+(?:\.\d+)?)\s*(?P<rk>k(?:ilo)?|m(?:ega)?)?\s*(?:ohms?|Ω|ω)", re.I)
_SPEAK_ONLY = re.compile(r"^(?:from now on,?\s+)?(?:please,?\s+)?(?:can you\s+|could you\s+)?(?:only\s+)?(?:speak|talk|use|answer in|reply in)\s+"
                         r"(?:only\s+|just\s+)?(?P<lang>english|french|fran[cç]ais|haitian creole|creole|krey[oò]l(?: ayisyen)?|spanish|espa[nñ]ol)"
                         r"(?:\s+only)?(?:\s+(?:with|to)\s+me)?(?:\s+from now on)?(?:,?\s+please)?[.!]*$", re.I)


_PAIR_MINE = re.compile(r"^(?:can you\s+|could you\s+)?(?:please\s+)?(?:pair|link|connect|set up)\s+(?:my|a new|my new)\s+phone"
                        r"(?:\s+(?:with|to) (?:you|jarvis))?(?:,?\s+please)?[.!]*$", re.I)


# Mark VI missions, live 2026-11-10: the brain never picked the new skills - "Report my disk space while I'm away" got
# made-up disk numbers instead of a queued mission, and "What are my missions?" got "Mission cancelled, sir." with no call.
# Plain mission sentences run the real skill.
_MISSION_WHILE = re.compile(r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?:can you\s+|could you\s+|please\s+)?(?P<goal>.{4,400}?)[,.]?\s+"
                            r"(?:while|when) i'?m (?:away|gone|out|asleep|sleeping|at work|gaming)[.!]*$|"
                            r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?:while|when) i'?m (?:away|gone|out|asleep|sleeping|at work|gaming),?\s+"
                            r"(?:can you\s+|could you\s+|please\s+)?(?P<goal2>.{4,400}?)[.!]*$", re.I)
_MISSION_STATUS = re.compile(r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?:what are|what'?s|how are|how'?s|show me|list|status of|any)\s+"
                             r"(?:my |the |all )?(?:missions?|mission status)(?: going| doing| status)?\??$", re.I)
_MISSION_CANCEL = re.compile(r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?:cancel|stop|drop|kill)\s+(?P<which>(?:the |that |my |this )?"
                             r"(?:[\w' -]{0,60}\s)?mission(?:\s+(?:about|to|for)\s+[\w' -]{1,60})?)[.!]*$", re.I)
_MISSION_PAUSE = re.compile(r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?P<verb>pause|hold|resume|unpause|continue|restart)\s+"
                            r"(?:my |the |all )?(?:of )?(?:the )?missions[.!]*$", re.I)


# Owner 2026-11-10: his words, not his keyboard, hold missions. Whole short statements only ("I'm at the PC" holds; "I'm not
# at the PC" releases; "what are you doing at the PC" is neither).
_IM = r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?:i'?m|i am)\s+"
_THE_PC = r"(?:the |my )?(?:pc|computer|desk)"
_PRESENCE_GAME = re.compile(_IM + r"(?:(?:gonna|going to|about to)\s+(?:go\s+)?(?:game|play(?:\s+(?:a\s+)?games?)?)|gaming"
                            r"(?:\s+now)?|playing(?:\s+(?:a\s+)?games?)?(?:\s+now)?)[.!]*$", re.I)
_PRESENCE_BUSY = re.compile(_IM + r"(?:(?:at|on|using|working on|back (?:at|on))\s+" + _THE_PC + r"(?:\s+now)?)[.!]*$", re.I)
_PRESENCE_FREE = re.compile(_IM + r"(?:done\s+(?:gaming|playing|with\s+" + _THE_PC + r")|off\s+" + _THE_PC +
                            r"|not\s+(?:at|on|using)\s+" + _THE_PC + r"|away(?:\s+from\s+" + _THE_PC + r")?)(?:\s+now)?[.!]*$|"
                            r"^(?:(?:hey\s+)?jarvis[,.]?\s+)?(?:you can|go ahead and)\s+(?:get back to\s+)?work(?:\s+on\s+"
                            r"(?:the |my )?missions)?(?:\s+now)?[.!]*$", re.I)


def _presence(raw: str) -> str | None:
    said = raw.replace("’", "'").strip()
    busy = (False, "") if _PRESENCE_FREE.match(said) else (True, "you're gaming") if _PRESENCE_GAME.match(said) else         (True, "you're at the PC") if _PRESENCE_BUSY.match(said) else None
    if busy is None:
        return None
    from tools import missions as mission_tools
    if not mission_tools._owner():
        return None            # a household member's "I'm gonna game" is ordinary conversation, not his missions
    return mission_tools.owner_presence(busy[0], busy[1])


def _mission_command(raw: str) -> str | None:
    from tools import registry
    said = _presence(raw)
    if said is not None:
        return said
    m = _MISSION_WHILE.match(raw)
    if m:
        goal = (m.group("goal") or m.group("goal2") or "").strip(" ,.")
        if len(goal.split()) >= 2:
            return _ran(registry.run("start_mission", {"goal": goal}))
    if _MISSION_STATUS.match(raw):
        return _ran(registry.run("mission_status", {}))
    m = _MISSION_CANCEL.match(raw)
    if m:
        which = re.sub(r"^(?:the|that|my|this)\s+|\bmission\b", " ", m.group("which"), flags=re.I).strip()
        return _ran(registry.run("cancel_mission", {"which": which}))
    m = _MISSION_PAUSE.match(raw)
    if m:
        return _ran(registry.run("pause_missions", {"paused": m.group("verb").lower() in ("pause", "hold")}))
    return None


# Known issue 31, live 2026-11-10: readings about the PC were invented ("running for four hours": 1 day 16 h; "GPU at sixty
# degrees": 46; "120 megabits down": never measured), and after that was blocked, guessed in words ("your CPU is running
# cool" - it can't even be read here). The brain never chose the status skill, so these are MEASURED, then said.
_PC_NOUN = r"(?:my |the |this )?(?:pc|computer|laptop|system|machine|cpu|processor|gpu|graphics card|video card|rig)"
_READ_TEMP = re.compile(rf"\b(?:how hot|temp(?:erature)?s?|running hot|overheat\w*|thermal)\b.*\b{_PC_NOUN}\b|"
                        rf"\b{_PC_NOUN}\b.*\b(?:temp(?:erature)?s?|running hot|hot|overheat\w*)\b", re.I)
_READ_UPTIME = re.compile(rf"\b(?:how long has|how long's)\s+{_PC_NOUN}\s+been (?:on|running|up)\b|\buptime\b", re.I)
_READ_VRAM = re.compile(r"\b(?:vram|video memory|graphics memory)\b", re.I)
_READ_LOAD = re.compile(rf"\b(?:cpu|processor|gpu)\s+(?:usage|load|utili[sz]ation)\b|\bhow (?:busy|hard) is\s+{_PC_NOUN}\b", re.I)
_READ_HEALTH = re.compile(rf"\b(?:health (?:report|check|status)|system status|status report|how is|how's)\b.*\b{_PC_NOUN}\b|"
                          rf"\b{_PC_NOUN}\b.*\b(?:health|status)\b", re.I)
# System memory: "How much RAM is free?" / "How much memory is free right now?" (live 2026-11-15, by voice: "I couldn't work out how to do ..." twice).
# Video memory has its own reading above; "wasting my memory" is a scan request, not a reading.
# "How much RAM is free?" and "How much RAM is on my PC?": one answer with both numbers. The learned RAM skill that once answered the
# second does not exist on every install (live 2026-11-15: "I couldn't work out how to do ..."), so the status answers it.
_READ_MEM = re.compile(
    r"\bhow much (?:ram|memory)(?:\s+(?:is|are|do i have|have i got|am i using|does (?:my|the|this) (?:pc|computer|machine|system) have))?"
    r"(?:\s+(?:free|left|available|in use|used|using|spare|open|installed|on (?:my|the|this) (?:pc|computer|machine|system)))?|"
    r"\bhow much (?:free|spare|available) (?:ram|memory)\b|"
    r"\bwhat(?:'s| is) (?:my |the |this )?(?:(?:free|available|spare) )?(?:ram|memory)\s+(?:usage|use|free|left|available)\b|"
    r"\bwhat(?:'s| is) (?:my|the) (?:free|available|spare) (?:ram|memory)\b|"
    r"\b(?:ram|memory)\s+(?:usage|use)\b|\bhow (?:full|much) is (?:my |the )?(?:ram|memory) (?:full|used|in use)\b|\bis (?:my|the) (?:ram|memory) (?:full|low)\b", re.I)
_MEM_TAIL = re.compile(r"[\s,.!?]*(?:(?:do i have|have i got|is there|right now|at the moment|currently|now|today|please|sir|jarvis|total|overall|altogether|on (?:my|the|this) (?:pc|computer|laptop|machine)|in (?:my|the|this) (?:pc|computer|laptop|machine))[\s,.!?]*)*", re.I)
# "How much RAM is free?" heard as "How much room is free?" (live 2026-11-16 23:55: it went to a web search). In that frame
# "room" is the PC's RAM as Whisper wrote it, or he really means room on the disk: both numbers are read, nothing is guessed.
# "How much space / storage is left?" is the disk. Only when that is the WHOLE line: "room in the car" is not it.
_ROOM_FREE = r"(?:\s+(?:is|do i have|have i got|is there|have i))?(?:\s+(?:free|left|available|spare|remaining|open|used|in use))?"
_READ_ROOM = re.compile(r"how much (?:free |spare )?room" + _ROOM_FREE + r"\b", re.I)
_READ_DISK = re.compile(r"how much (?:free |spare )?(?:(?:disk|drive|hard drive|ssd) )?(?:space|storage)" + _ROOM_FREE + r"\b|"
                        r"how much (?:free |spare )?(?:disk|drive|hard drive|ssd)(?: space)?" + _ROOM_FREE + r"\b", re.I)
_READ_NET = re.compile(r"\bhow fast is (?:my |the )?(?:internet|wi-?fi|connection|network)\b|\binternet speed\b|\bspeed ?test\b", re.I)


def _pc_reading(raw: str) -> str | None:
    raw = re.sub(r"(?i)^\W*(?:(?:so|well|okay|ok|and|um|uh|also|then|hey jarvis|jarvis)[,.\s]+)+", "", raw)   # "So how much RAM is free." was answered with the TOTAL by the model (live 2026-11-15)
    if raw.endswith("?") is False and not re.match(r"(?i)^(?:how|what|is|give|tell|check|show|any)\b", raw):
        return None                                   # only questions and requests to report, never a statement
    if _READ_NET.search(raw):
        return "I haven't measured your internet speed, sir; I don't have a speed test yet, so I won't guess."
    asked = [k for k, rx in (("temp", _READ_TEMP), ("uptime", _READ_UPTIME), ("vram", _READ_VRAM), ("load", _READ_LOAD),
                             ("health", _READ_HEALTH)) if rx.search(raw)]
    for key, rx in (("room", _READ_ROOM), ("disk", _READ_DISK)):
        m = rx.match(raw)
        if m and _MEM_TAIL.fullmatch(raw[m.end():]):
            asked.append(key)                         # the whole line is that question, and nothing else
    if asked or _READ_MEM.search(raw):
        from tools import registry as _registry
        if "get_system_status" not in _registry.names():
            return None                               # this machine cannot read a PC (the laptop): the turn goes on to the PC
        if re.search(r"\b(?:laptop|phone|server)\b", raw, re.I):
            return None                               # a reading of another machine is not this PC's
    mem = _READ_MEM.search(raw)
    if mem and "vram" not in asked and _MEM_TAIL.fullmatch(raw[mem.end():]):
        asked.append("mem")                           # the whole PC's memory, and nothing else asked ("...is the game using?" is not it)
    if asked and "mem" not in asked and re.search(r"\b(?:ram|memory)\b", raw, re.I) and not _READ_VRAM.search(raw):
        return None                                   # "CPU usage and RAM?": a half answer would drop what he asked
    if not asked:
        return None
    from tools import registry
    s, err = registry.run("get_system_status", {})
    if isinstance(s, str):
        try:
            import json as _json
            s = _json.loads(s)                        # the registry hands a dict result back as JSON text
        except ValueError:
            s = None
    if err or not isinstance(s, dict):
        return "I couldn't read the PC's status just now, sir, so I won't guess."
    parts = []
    want = set(asked) | ({"temp", "uptime", "vram", "load"} if "health" in asked else set())
    if "temp" in want:
        parts.append(f"your graphics card is at {s['gpu_temperature_c']} degrees" if "gpu_temperature_c" in s
                     else "I can't read the graphics card's temperature")
        if re.search(r"\b(?:cpu|processor)\b", raw, re.I) or "health" in asked:
            parts.append("I can't read the CPU temperature on this PC")
    if "load" in want:
        parts.append(f"the CPU is at {round(s['cpu_percent'])} percent load"
                     + (f" and the graphics card at {s['gpu_load_percent']} percent" if "gpu_load_percent" in s else ""))
    if "vram" in want and "vram_free_gb" in s:
        parts.append(f"{s['vram_free_gb']} GB of video memory is free, of {s['vram_total_gb']}")
    if "mem" in asked and "health" not in asked:
        if "memory_used_gb" in s and "memory_total_gb" in s:
            free = round(float(s["memory_total_gb"]) - float(s["memory_used_gb"]), 1)
            parts.append(f"{free} GB of memory is free, with {s['memory_used_gb']} of {s['memory_total_gb']} GB in use")
        else:
            parts.append("I can't read the memory just now")
    if "room" in asked and "mem" not in asked and "health" not in asked:
        if "memory_used_gb" in s and "memory_total_gb" in s:
            free = round(float(s["memory_total_gb"]) - float(s["memory_used_gb"]), 1)
            parts.append(f"{free} GB of memory is free, of {s['memory_total_gb']} GB")
    if ("room" in asked or "disk" in asked) and "health" not in asked:
        parts.append(f"{round(s['disk_free_gb'])} GB of disk is free" if "disk_free_gb" in s else "I can't read the disk just now")
    if "health" in asked:
        parts.append(f"{s['memory_used_gb']} of {s['memory_total_gb']} GB of memory is in use and {round(s['disk_free_gb'])} GB of disk is free")
    if "uptime" in want:
        h = s["uptime_hours"]
        days, hours = int(h // 24), int(round(h % 24))
        parts.append("it has been on for " + (f"{days} day{'s' if days != 1 else ''} and " if days else "") + f"{hours} hour{'s' if hours != 1 else ''}")
    text = "; ".join(parts)
    return (text[:1].upper() + text[1:] + ", sir.") if text else None


def _ran(result) -> str:
    text, _err = result
    return text if isinstance(text, str) else str(text)


def _speak_only(raw: str) -> str | None:
    m = _SPEAK_ONLY.match(raw.strip())
    if not m:
        return None
    lang = m.group("lang").casefold()
    name = {"creole": "Haitian Creole", "kreyol": "Haitian Creole", "kreyòl": "Haitian Creole", "kreyol ayisyen": "Haitian Creole",
            "kreyòl ayisyen": "Haitian Creole", "haitian creole": "Haitian Creole", "francais": "French", "français": "French",
            "espanol": "Spanish", "español": "Spanish"}.get(lang, lang.capitalize())
    from tools import registry
    result, _ok = registry.run("set_conversation_language", {"language": name})
    text = result if isinstance(result, str) else str(result)
    if "isn't running" in text:
        # Nothing was locked here (a phone or text turn): say so instead of claiming a change (review 2026-11-10).
        return f"Speech recognition isn't running here, so there's nothing to lock, sir. Write to me in {name} and I'll answer in {name}."
    return (f"Understood, sir. I'll listen for {name} from now on. Say 'automatic language' to undo it."
            if "locked" in text else text)


# -- the common ones asked in Haitian Creole, French or Spanish: answered in that language ---------------------------
# He switches language by speaking it (audio.language). The time, the date and a timer have one right answer in any
# language; the English patterns above never matched "Ki lè li ye?", so the model was left to guess the hour.
_POLITE = r"(?:\s+(?:kounye a|tanpri|souple|silvouplè|mesye|maintenant|s'il (?:vous|te) pla[iî]t|monsieur|ahora|por favor|señor|jarvis))*"
_START = r"(?:(?:[eè]ske|est-ce que|oye|dis-moi|dime|di m|jarvis)\s+)*"
_CLOCK_IN = {
    "ht": r"(?:ki|kèl) ?l[èe] (?:li|l|nou) (?:ye|fè)|kil[èe] (?:li|l) ye",
    "fr": r"quelle heure (?:est[- ]il|il est)|il est quelle heure|(?:tu as|vous avez|t'as) l'heure",
    "es": r"qu[ée] horas? (?:es|son)|(?:me dices|tienes|tiene) la hora",
}
_DATE_IN = {
    "ht": r"ki (?:dat|jou) (?:li|nou) ye(?: jodi a)?|ki dat jodi a(?: ye)?|jodi a se ki (?:dat|jou)",
    "fr": r"quel(?:le)? (?:jour|date) (?:sommes[- ]nous|on est|est[- ]on|est[- ]ce)(?: aujourd'hui)?|on est quel jour"
          r"(?: aujourd'hui)?|c'est quoi la date(?: d'aujourd'hui)?|quelle est la date(?: d'aujourd'hui)?",
    "es": r"qu[ée] (?:d[ií]a|fecha) es(?: hoy)?|a qu[ée] (?:d[ií]a|fecha) estamos(?: hoy)?|cu[aá]l es la fecha(?: de hoy)?",
}
_TIMER_IN = {
    "ht": r"\b(?:mete|met|kòmanse|komanse|fè|ban m|ban mwen)\b.*\b(?:revèy|revey|minitè|minite|kwonomèt|timer)\b",
    "fr": r"\b(?:mets|mettez|lance|lancez|démarre|règle|programme)\b.*\b(?:minuteur|minuterie|chrono\w*|timer)\b",
    "es": r"\b(?:pon|ponme|pone|inicia|configura|programa)\b.*\b(?:temporizador|cron[oó]metro|timer)\b",
}
_NUM_IN = {"en": 1, "youn": 1, "yon": 1, "de": 2, "twa": 3, "kat": 4, "senk": 5, "sis": 6, "sèt": 7, "uit": 8, "nèf": 9,
           "dis": 10, "kenz": 15, "ven": 20, "trant": 30, "karant": 40, "karannsenk": 45, "swasant": 60,
           "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
           "dix": 10, "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40, "quarante-cinq": 45, "soixante": 60,
           "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
           "diez": 10, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40, "sesenta": 60,
           "senkant": 50, "cinquante": 50, "cincuenta": 50}
# Words of numbers the table does not read: a number built on one of them is not guessed at.
_BIG_IN = {"san", "mil", "cent", "cents", "mille", "cien", "ciento", "cientos", "swasanndis", "katreven", "katrevendis",
           "setenta", "ochenta", "noventa", "septante", "octante", "nonante"}
_DAYS_IN = {"fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
            "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]}
_MONTHS_IN = {"fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                     "novembre", "décembre"],
              "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                     "noviembre", "diciembre"]}


def _asked_in(patterns: dict, bare: str, whole: bool = True) -> str | None:
    for code, pattern in patterns.items():
        found = re.fullmatch(_START + "(?:" + pattern + ")" + _POLITE, bare, re.I) if whole else re.search(pattern, bare, re.I)
        if found:
            return code
    return None


def clock_in(code: str, now) -> str:
    """The time of day said in Haitian Creole, French or Spanish."""
    if code == "ht":
        from audio import kreyol
        return f"Li {kreyol.clock(now.hour, now.minute)}."
    if code == "fr":
        if not now.minute and now.hour % 12 == 0:
            return "Il est minuit." if now.hour == 0 else "Il est midi."
        from core.words import plural
        return f"Il est {plural(now.hour, 'heure')}." if not now.minute else f"Il est {now.hour} h {now.minute:02d}."
    if not now.minute and now.hour % 12 == 0:
        return "Es medianoche." if now.hour == 0 else "Es mediodía."
    hour = now.hour % 12 or 12
    part = ("de la madrugada" if now.hour < 6 else "de la mañana" if now.hour < 12 else "de la tarde" if now.hour < 20
            else "de la noche")
    return f"{'Es la' if hour == 1 else 'Son las'} {hour}:{now.minute:02d} {part}."


def date_in(code: str, now) -> str:
    if code == "ht":
        from audio import kreyol
        return f"Jodi a se {kreyol.date(now)}."
    day, month = _DAYS_IN[code][now.weekday()], _MONTHS_IN[code][now.month - 1]
    if code == "fr":
        return f"Nous sommes le {day} {'1er' if now.day == 1 else now.day} {month}."
    return f"Hoy es {day} {now.day} de {month}."


_UNIT_IN = re.compile(r"minit|minutes?|minutos?|segonn|secondes?|segundos?|èdtan|edtan|heures?|horas?")
_JOIN_IN = {"y", "et"}                                             # treinta y cinco, trente et un
_SMALL_IN = {"en", "de", "un", "une", "una", "yon", "youn"}        # also plain words ("de cinco minutos", "en cinco minutos")
_HALF_IN = (("edmi",), ("e", "demi"), ("et", "demi"), ("et", "demie"), ("y", "media"), ("y", "medio"))


def _number_in(words: list) -> int:
    """A number of one word ('dix'), or tens and units ('trant senk', 'vingt-cinq', 'treinta y cinco'); 0 otherwise."""
    if len(words) == 1:
        return int(words[0]) if words[0].isdigit() else _NUM_IN.get(words[0], 0)
    if len(words) == 3 and words[1] in _JOIN_IN:
        words = [words[0], words[2]]
    if len(words) == 2 and "-".join(words) in _NUM_IN:
        return _NUM_IN["-".join(words)]
    if len(words) == 2:
        tens, units = _NUM_IN.get(words[0], 0), _NUM_IN.get(words[1], 0)
        return tens + units if tens in (20, 30, 40, 50, 60) and 1 <= units <= 9 else 0
    return 0


def _duration_in(text: str) -> int:
    """The seconds a line asks for, or 0 when any part of the number is not surely understood (review 2026-11-12:
    'trant senk minit' was read as five minutes and the timer was really set). A wrong timer is worse than none:
    on 0 the line goes to the model instead."""
    words = re.findall(r"\d+|[^\W\d_]+", text.lower())
    total, used, first = 0, set(), None
    for i, word in enumerate(words):
        if not _UNIT_IN.fullmatch(word):
            continue
        first = i if first is None else first
        value = size = 0
        for size in (3, 2, 1):                                     # the longest number that ends at the unit
            value = _number_in(words[i - size:i]) if i >= size else 0
            if value:
                break
        if not value:
            return 0
        before = words[i - size - 1] if i > size else ""
        if before.isdigit() or (before in _NUM_IN and before not in _SMALL_IN):
            return 0                                               # 'san ven', 'quatre vingt': part of a bigger number
        if before in _JOIN_IN and not (i > size + 1 and _UNIT_IN.fullmatch(words[i - size - 2])):
            return 0                                               # 'setenta y cinco': the tens were not understood
        if i > size and words[i - size - 1] in _BIG_IN:
            return 0
        used.update(range(i - size, i))
        unit = 60 if word.startswith("min") else 1 if word.startswith("se") else 3600
        total += value * unit
        for half in _HALF_IN:
            if tuple(words[i + 1:i + 1 + len(half)]) == half:
                total += unit // 2
                used.update(range(i + 1, i + 1 + len(half)))
    if first is not None and any((w.isdigit() or (w in _NUM_IN and w not in _SMALL_IN)) and j not in used
                                 for j, w in enumerate(words) if j > first):
        return 0                                                   # 'une heure trente': a number left over
    return total


def _say_dur_in(code: str, seconds: int) -> str:
    from core.words import plural
    units = {"ht": ("èdtan", "minit", "segonn"), "fr": ("heure", "minute", "seconde"), "es": ("hora", "minuto", "segundo")}[code]
    parts = [(seconds // 3600, units[0]), (seconds % 3600 // 60, units[1]), (seconds % 60, units[2])]
    return " ".join(plural(n, word, word if code == "ht" else None) for n, word in parts if n)   # Creole nouns do not change


# A greeting and/or his name in front of a question ("Hola, ¿qué hora es?", "Bonjou Jarvis, ki lè li ye?").
_GREETING = re.compile(r"^(?:(?:hola|bonjou|bonswa|bonjour|bonsoir|salut|coucou|hello|hey|hi|oye|buen(?:os|as)(?: d[ií]as| tardes| noches)?"
                       r"|jarvis)\s+)+", re.I)


def _without_greeting(bare: str) -> str:
    cleaned = re.sub(r"\s+", " ", bare.replace("¿", " ").replace("¡", " ")).strip(" ?.!")
    stripped = _GREETING.sub("", cleaned + " ", count=1).strip(" ?.!")
    return stripped or cleaned


def _in_their_language(bare: str) -> str | None:
    bare = _without_greeting(bare)
    code = _asked_in(_CLOCK_IN, bare)
    if code:
        return clock_in(code, datetime.now())
    code = _asked_in(_DATE_IN, bare)
    if code:
        return date_in(code, datetime.now())
    code = _asked_in(_TIMER_IN, bare, whole=False)
    if code:
        from core import pcbridge
        seconds = _duration_in(bare)
        if not pcbridge.local_pc() or not 1 <= seconds <= 86400:
            return None
        from tools import system
        system.set_timer(seconds, "timer")
        length = _say_dur_in(code, seconds)
        return {"ht": f"Dakò, revèy la mete pou {length}. M ap di ou lè li fini.",
                "fr": f"C'est noté : minuteur de {length}. Je vous préviens à la fin.",
                "es": f"Listo: temporizador de {length}. Le aviso cuando termine."}[code]
    return None


_ASKS_NAME = re.compile(r"^\W*(?:(?:so|well|okay|ok|and|um|uh)[,.\s]+)*(?:what(?:'s| is| are)(?: exactly)? your name|what should i call you|"
                        r"what do you go by|do you have a name|what do (?:i|we|people) call you)(?:\s+again)?(?:[,\s]+(?:jarvis|sir|then))?\W*$", re.I)
_ASKS_WHO = re.compile(r"^\W*(?:(?:so|well|okay|ok|and|um|uh)[,.\s]+)*(?:who|what)(?: are|'re| exactly are) you(?: exactly)?(?:\s+again)?"
                       r"(?:[,\s]+(?:jarvis|sir|then))?\W*$|^\W*(?:introduce yourself|tell me about yourself)\W*$", re.I)


def _identity(raw: str) -> str | None:
    """'What's your name?' / 'Who are you?' (live 2026-11-15, by voice: seven rambling sentences about circuits and tone): one short line."""
    try:
        from core import users
        title = users.title(users.current(), "en") or "sir"
    except Exception:  # noqa: BLE001
        title = "sir"
    if _ASKS_NAME.match(raw):
        return f"Jarvis, {title}."
    if _ASKS_WHO.match(raw):
        return f"I'm Jarvis, {title}: your assistant on this PC, your phone and the house."
    return None


_ASKS_STATUS = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|um|uh|hey jarvis|jarvis)[,.\s]+)*(?:are you(?: still)? (?:working on|doing|done with|finished with) (?:it|that|this)|"
    r"did you (?:finish|work on) (?:it|that|this)|any (?:progress|updates?) on (?:it|that|this)|what(?:'s| is) the (?:status|progress) (?:of|on) (?:it|that|this))"
    r"(?:[,\s]+(?:yet|now|jarvis|sir))*\W*$", re.I)


def _status(raw: str) -> str | None:
    """"Are you working on it?" / "Is it ready yet?": answered from what is really running. The model said "Yes, sir." with nothing
    running (live 2026-11-15, by voice, after "I am creating the humanoid" eight times). A mission that really runs is the model's to report."""
    if not _ASKS_STATUS.match(raw):
        return None
    try:
        from core import craftjobs, missions, pcbridge, progress
        if not pcbridge.local_pc():
            # The laptop cannot see what the PC is doing, and the model there said "Yes, sir." (live 2026-11-15): the PC's own status answers it.
            try:
                from core import pcfast
                return pcfast.ask_pc(raw) or None
            except Exception:  # noqa: BLE001 - unknown: the model answers, as before
                return None
        job = craftjobs.get()
        if (any(m.get("state") != "queued" for m in missions.listing(active_only=True, limit=20))   # a queued one is not being done
                or (job and job.get("state") in ("running", "queued")) or progress._now.get("what")):
            return None                       # something known is running: the model, with its tools, reports it
    except Exception:  # noqa: BLE001 - unknown: the model answers, as before
        return None
    try:
        from core import users
        title = users.title(users.current(), "en") or "sir"
    except Exception:  # noqa: BLE001
        title = "sir"
    return f"Nothing is running that I know of, {title}. Tell me what you want done and I'll do it."


# "Tell me about the weather tomorrow." was answered "I don't have the forecast for tomorrow yet... say yes and I'll get it" (live 2026-11-16, his own
# words): the forecast is one skill call, so it is made here, for his own town, today or tomorrow only.
_ASKS_WEATHER = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|please|um|uh)[,.\s]+)*(?:"
    r"(?:tell me|give me|show me|what(?:'s| is)|how(?:'s| is)|any)\s+(?:about\s+)?(?:the\s+|my\s+)?(?:weather|forecast|temperature)(?:\s+(?:like|outside|looking|here))*"
    r"|(?:what(?:'s| is)|how(?:'s| is)) it(?: looking| like)? outside|(?:will|is) it (?:going to )?(?:rain|snow|be (?:cold|hot|warm|sunny|rainy|cloudy|windy))|"
    r"(?:do i|should i) (?:need|bring|take|wear) (?:an? )?(?:umbrella|jacket|coat))"
    r"(?:[,\s]+(?:for\s+)?(?P<when>today|tonight|tomorrow|now|right now|this (?:morning|afternoon|evening)))?(?:[,\s]+(?:sir|jarvis|please))*\W*$", re.I)


def _weather(raw: str) -> str | None:
    m = _ASKS_WEATHER.match(raw.strip())
    if not m:
        return None
    try:
        from tools import registry
        if "get_weather" not in registry.names():
            return None
        out, err = registry.run("get_weather", {"place": "", "days": 2})
    except Exception:  # noqa: BLE001 - unknown: the model answers, as before
        return None
    text = str(out or "")
    if err or "don't know where home is" in text or not text.strip():
        return None
    try:
        from core import users
        title = users.title(users.current(), "en") or "sir"
    except Exception:  # noqa: BLE001
        title = "sir"
    when = (m.group("when") or "").lower()
    want = "Tomorrow" if when == "tomorrow" else "Today"
    line = next((ln for ln in text.splitlines() if ln.startswith(want + ":")), "")
    town = text.splitlines()[0].split(" now:")[0].strip() if " now:" in text.splitlines()[0] else ""
    parsed = re.match(r"^(?:Today|Tomorrow):\s*(?P<sky>[^,]+),\s*high\s*(?P<hi>\d+)\D+low\s*(?P<lo>\d+)\D+?(?P<rain>\d+)%", line)
    if not parsed:
        return None
    say = f"{parsed.group('sky')}, a high of {parsed.group('hi')}, a low of {parsed.group('lo')} and a {parsed.group('rain')} percent chance of rain"
    now = ""
    if want == "Today" and when not in ("tonight",):
        cur = re.search(r" now:\s*([^,]+),\s*(\d+)\D", text.splitlines()[0])
        if cur:
            now = f"Right now it's {cur.group(1)} and {cur.group(2)} degrees. "
    label = "Tomorrow" if want == "Tomorrow" else "Today"
    return f"{now}{label}{' in ' + town if town else ''}: {say}, {title}."


# A question about his Google Calendar is answered by the calendar skill itself, never by the model: before the calendar was
# connected "What's on my calendar today?" got "Nothing on your calendar today, sir. Just a clear day" (live 2026-11-16, an
# invented fact about his day), and the one brain never calls a skill for "Am I free on Tuesday?" (sandbox 2026-11-20). The
# skill says plainly when the calendar isn't connected. Only a clear question with a day we can name exactly is taken here;
# "Add a meeting to my calendar" and every other line go to the brain as before.
_CAL_FILLER = r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|please|um|uh|sir)[,.\s]+)*"
_CAL_OBJ = r"(?:my |the |on my |in my )?(?:calendar|schedule|agenda|appointments|meetings|plans)"
_CAL_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_CAL_WHEN = (r"(?:for |on )?(?P<when>today|tonight|tomorrow|this (?:week|morning|afternoon|evening|weekend)|next week|now|"
             r"(?:this |next )?(?:" + "|".join(_CAL_DAYS) + r"))")
_CAL_TAIL = r"(?:[,\s]+(?:sir|jarvis|please))*\W*$"
# "What's on my calendar (today)?", "Check my schedule.", "What do I have on my calendar this week?"
_ASKS_CALENDAR = re.compile(
    _CAL_FILLER + r"(?:what(?:'s| is| do i have| have i got)(?: on)?|do i have (?:anything|any \w+)(?: on)?|is there anything(?: on)?|"
    r"am i (?:busy|free)|tell me(?: what(?:'s| is))?(?: on)?|check|read me|show me|what are)\s+" + _CAL_OBJ
    + r"(?:\s+" + _CAL_WHEN + r")?" + _CAL_TAIL, re.I)
# "Am I free on Tuesday?", "What do I have tomorrow?", "Do I have anything this weekend?", "Am I busy tonight?"
_ASKS_DAY = re.compile(
    _CAL_FILLER + r"(?:am i (?:busy|free)|what do i have(?: going on| on)?|what have i got(?: on)?|do i have anything(?: going on| on)?|"
    r"is there anything on)\s+" + _CAL_WHEN + _CAL_TAIL, re.I)


def calendar_days(when: str, today):
    """(first, last) dates for a spoken day, from today."""
    from datetime import timedelta
    w = (when or "today").lower().strip()
    if w in ("today", "tonight", "now") or w.startswith("this m") or w.startswith("this a") or w.startswith("this e"):
        return today, today
    if w == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1)
    if w == "this week":
        return today, today + timedelta(days=6 - today.weekday())
    if w == "next week":
        monday = today + timedelta(days=7 - today.weekday())
        return monday, monday + timedelta(days=6)
    if w == "this weekend":
        sat = today + timedelta(days=(5 - today.weekday()) % 7) if today.weekday() != 6 else today
        return sat, (sat + timedelta(days=1)) if sat.weekday() == 5 else sat
    name = w.split()[-1]
    ahead = (_CAL_DAYS.index(name) - today.weekday()) % 7
    if w.startswith("next ") and ahead == 0:
        ahead = 7
    day = today + timedelta(days=ahead)
    return day, day


def _calendar(raw: str) -> str | None:
    line = raw.strip().replace("’", "'")
    m = _ASKS_CALENDAR.match(line) or _ASKS_DAY.match(line)
    if not m:
        return None
    from core import ownertime
    from tools import gcalendar
    first, last = calendar_days(m.group("when"), ownertime.now().date())
    return gcalendar.calendar_events(first.isoformat(), last.isoformat(), "")


# "Make sure I'm not getting hacked because I downloaded something" was answered "I have checked your recent downloads and activity logs, sir;
# nothing looks like a hack" with NOTHING run (live 2026-11-16, his own words). The read-only checkup (tools/security.py) is one call.
_ASKS_HACKED = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|please|um|uh)[,.\s]+)*(?:"
    r"(?:make sure|check|see|tell me|find out)(?: (?:if|whether|that))? (?:i'?m|i am|my (?:pc|computer|laptop) (?:is|isn'?t)) (?:not )?(?:getting |being |been )?(?:hacked|infected|compromised)|"
    r"(?:am i|is my (?:pc|computer|laptop)|has my (?:pc|computer|laptop) been) (?:getting |being |been )?(?:hacked|infected|compromised)|"
    r"(?:scan|check) (?:my (?:pc|computer|laptop) )?for (?:malware|viruses|a virus|spyware|hackers)|do i have (?:a )?(?:virus|malware|spyware)|"
    r"run (?:a |the )?(?:security|virus|malware) (?:check|checkup|scan))"
    r"(?:\s+(?:because|since|after|cause)\b[^.?!]*)?(?:\s+or whatever)?(?:[,\s]+(?:sir|jarvis|please))*\W*$", re.I)


# "My computer keeps opening my files folder by itself. Can you tell me why?" -> "I checked the startup tasks and scheduled jobs, sir; nothing is set to
# launch it" with nothing run (live 2026-11-16): the real checkup runs, and he says plainly what it cannot tell him.
_KEEPS_OPENING = re.compile(r"\b(?:keeps?|keep)\s+(?:on\s+)?(?:opening|popping up|launching|starting|showing up|appearing)\b[^.?!]{0,60}\b(?:by itself|on its own|randomly|out of nowhere|every \w+ minutes?)\b"
                            r"|\b(?:opens?|pops? up|launches)\s+(?:by itself|on its own|randomly|out of nowhere)\b", re.I)
_CHECKUP_LIMIT = " That doesn't tell me what is opening it, sir; the next time it appears, tell me and I'll look at what is running."


def _security(raw: str) -> str | None:
    keeps = bool(_KEEPS_OPENING.search(raw)) and raw.strip().endswith("?") or bool(_KEEPS_OPENING.search(raw)) and re.search(r"(?i)\b(?:why|tell me|can you)\b", raw)
    if not (keeps or _ASKS_HACKED.match(raw.strip())):
        return None
    try:
        from tools import registry
        if "security_checkup" not in registry.names():
            from core import pcfast                      # the laptop cannot see the PC's tasks and startup: the PC runs its own checkup
            return pcfast.ask_pc(raw) or None            # (the PC adds the same limit sentence itself)
        out, err = registry.run("security_checkup", {})
    except Exception:  # noqa: BLE001 - unknown: the model answers, as before
        return None
    text = str(out or "").strip()
    return None if err or not text else (text + _CHECKUP_LIMIT if keeps and "ask_pc" not in repr(out) else text)


# "So if I purchase credits, is that for you?" -> "credits ... pay for my cloud thinking" and "did you use any API?" -> a 41 s answer about a guide on his
# screen (live 2026-11-16): he runs on local models only, and says so.
_ASKS_CREDITS = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|please|um|uh)[,.\s]+)*(?:"
    r"(?:if|when|should|do) i (?:need to |have to |want to )?(?:purchase|buy|add|pay for|top up)(?: more| some)? (?:api |cloud |anthropic )?(?:credits|tokens)(?:,? is that for you)?|"
    r"(?:is|are) (?:the |those |these )?(?:credits|tokens|the api|the subscription) for you|do you (?:need|use|want) (?:any )?(?:credits|api|the api|tokens|cloud)|"
    r"(?:did|have) you (?:just )?(?:use|used|using|call|called|spend|spent|cost me)(?: (?:any|the|an?|a paid))? ?(?:api|credits|tokens|money|cloud)?(?: (?:calls?|credits|tokens|money))?"
    r"(?: by (?:trying|doing|running) (?:to do )?that)?|are you (?:using|costing|charging)(?: me)?(?: (?:any|the|an?))? ?(?:api|credits|money|cloud)?|"
    r"did (?:that|this) cost (?:me )?(?:anything|any money|money|credits)|"
    r"(?:how much|how many) (?:usage|tokens|credits?|budget)(?: (?:are we at|do we have|do i have|have we used|is left|are left|remain))?(?: for you)?(?: right now)?(?:[.?]\s*how much more)?|"
    r"(?:the )?(?:cloud|api) budget(?:,? how do i do that)?|"
    r"how much (?:money|credit|credits|balance) (?:do we have|do i have|is|are there|have we got)(?: left| remaining)?(?: for (?:you|the api|the cloud))?(?: right now)?(?: to use)?(?: the)? ?(?:cloud|api)?(?:,? uh,?)?(?: cloud)?(?: or whatever)?)\W*$", re.I)


def _credits(raw: str) -> str | None:
    if not _ASKS_CREDITS.match(raw.strip()) or not re.search(r"(?i)\b(?:api|credits?|tokens?|cost|money|cloud|paid|subscription|usage|budget)\b", raw):
        return None
    try:
        from core import intelligence, users
        if not intelligence.local_only():
            return None                                  # the paid cloud is on: the model's own answer, with its real settings
        title = users.title(users.current(), "en") or "sir"
    except Exception:  # noqa: BLE001
        return None
    return (f"No, {title}. I think on local models on your PC, with the paid cloud switched off, so nothing I say costs you anything and credits "
            f"don't pay for me. They would only matter if you turned the cloud back on.")


# "Hey Jarvis, can you see my screen?" went to the PC planner as a command and came back "I couldn't work out how to do 'Hey Jarvis, can you see my
# screen', sir" (live 2026-11-16, his own words): it is a question about what he can do, answered as one.
_ASKS_SEE_SCREEN = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|hey jarvis|jarvis|please|um|uh)[,.\s]+)*(?:can|could|do|are) you (?:actually |really )?(?:see|look at|watch|read)(?:ing)? "
    r"(?:my|the) (?:screen|monitor|display|desktop)(?:\s+(?:right now|now))?(?:[,\s]+(?:sir|jarvis))*\W*$", re.I)


def _see_screen(raw: str) -> str | None:
    if not _ASKS_SEE_SCREEN.match(raw.strip()):
        return None
    return "Yes, sir, when you ask I look at it. Say \"what's on my screen\" and I'll tell you what I see."


# "I am on a page here, but it's saying to pair my phone." -> "I'll give you a fresh code right now: 78, 11, 97" (an invented code, twice now): the code
# comes from pair_phone, so he is told how to get a real one.
_PAIR_PROMPT = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|uh|um|hey jarvis|jarvis)[,.\s]+)*(?:yes,?\s+)?(?:i(?:'m| am) on (?:a|the) (?:page|screen)(?: here)?,? but )?"
    r"it(?:'s| is) (?:saying|asking|telling me|prompting me)(?: me)? to (?:pair|link|connect) (?:my|the) phone\W*$", re.I)


def _pair_prompt(raw: str) -> str | None:
    if not _PAIR_PROMPT.match(raw.strip()):
        return None
    return "I'll make you a real pairing code, sir. Say \"pair my phone\" and I'll give it to you, and it works once."


# "No, it's all good. Give me a new code." -> "Use that to pair with the hub." (no code at all, live 2026-11-16): the code is made by pair_phone, for the owner.
_ASKS_NEW_CODE = re.compile(
    r"^\W*(?:(?:no|yes|okay|ok|so|well|and|hey jarvis|jarvis)[,.\s]+)*(?:it'?s all good[,.\s]+)?(?:give me|get me|make me|i need|can i (?:get|have)|can you (?:give|make) me)\s+"
    r"(?:a |another |one )?(?:new |fresh |pairing |one-time |phone )*code\W*$", re.I)


def _new_code(raw: str) -> str | None:
    if not _ASKS_NEW_CODE.match(raw.strip()):
        return None
    try:
        from core import users
        from tools import registry
        if users.get(users.current()).get("role") != "owner" or "pair_phone" not in registry.names():
            return None
        out, err = registry.run("pair_phone", {"person": "me"})
    except Exception:  # noqa: BLE001 - unknown: the model answers, as before
        return None
    text = str(out or "").strip()
    return None if err or not text else text


# "No, I'm saying what time will it get cooler today?" -> "It will be around 5:00 PM" (invented, live 2026-11-16): the forecast he has is daily, not hourly.
_ASKS_COOLER = re.compile(
    r"^\W*(?:(?:no|yes|okay|ok|so|well|and|hey jarvis|jarvis|i'?m saying)[,.\s]+)*(?:what time|when)(?: of day)?(?: will| does| is| should)? it(?: get| be| going to get| gonna get)?(?: (?:cooler|colder|warmer|hotter|"
    r"cool down|warm up|stop raining|start raining|rain|snow|stop snowing))(?:\s+(?:today|tonight|tomorrow))?(?:[,\s]+(?:sir|jarvis))*\W*$", re.I)


def _cooler(raw: str) -> str | None:
    if not _ASKS_COOLER.match(raw.strip()):
        return None
    try:
        from tools import registry
        if "get_weather" not in registry.names():
            return None
        out, err = registry.run("get_weather", {"place": "", "days": 2})
        from core import users
        title = users.title(users.current(), "en") or "sir"
    except Exception:  # noqa: BLE001
        return None
    line = next((ln for ln in str(out or "").splitlines() if ln.startswith("Today:")), "")
    parsed = re.match(r"^Today:\s*[^,]+,\s*high\s*(?P<hi>\d+)\D+low\s*(?P<lo>\d+)", line)
    if err or not parsed:
        return None
    return (f"I only have the daily forecast, {title}, not the hourly one: a high of {parsed.group('hi')} and a low of {parsed.group('lo')} today. "
            f"So I can't tell you what time it changes.")


def handle(text: str) -> str | None:
    raw = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", (text or "").strip(), flags=re.I)
    raw = re.sub(r"^(?:hey\s+)?jarvis[,\s]+", "", raw, flags=re.I).strip()
    # Commands the user taught ("when I say blue notebook, reply with noted"): executed, learned, listed and forgotten
    # here, exactly and before any model (core/commands.py). They were left to the model's memory of the chat and
    # did not survive a restart.
    from core import commands
    taught = commands.handle(raw)
    if taught is not None:
        return taught
    try:       # he repeats an order that was not carried out: owned and done, never filler (core/followthrough)
        from core import followthrough
        owed = followthrough.handle(raw)
    except Exception as error:  # noqa: BLE001 - unknown: the protocols answer, as before
        print(f"[followthrough] {type(error).__name__}: {error}")
        owed = None
    if owed is not None:
        return owed
    try:       # "gaming protocol", "what protocols do I have", "create a protocol called ...": data, run by core/protocols
        from core import protocols
        proto = protocols.handle(raw)
    except Exception as error:  # noqa: BLE001 - unknown: the model answers, as before
        print(f"[protocols] {type(error).__name__}: {error}")
        proto = None
    if proto is not None:
        return proto
    try:       # "go live", "end the stream", "am I live": OBS on the PC (core/obsstream), never a guess by the model
        from core import obsstream
        stream = obsstream.handle(raw)
    except Exception as error:  # noqa: BLE001
        print(f"[obsstream] {type(error).__name__}: {error}")
        stream = None
    if stream is not None:
        return stream
    try:       # "stop doing that" after a learned protocol ran or was suggested; "what habits have you noticed" (core/habits)
        from core import habits
        habit = habits.handle(raw)
    except Exception as error:  # noqa: BLE001
        print(f"[habits] {type(error).__name__}: {error}")
        habit = None
    if habit is not None:
        return habit
    try:       # "what are my goals", "my goal is ...", "what do you owe me": his goals and Jarvis's open promises (core/commitments)
        from core import commitments
        partner = commitments.handle(raw)
    except Exception as error:  # noqa: BLE001
        print(f"[commitments] {type(error).__name__}: {error}")
        partner = None
    if partner is not None:
        return partner
    try:       # "note for the game: ..." -> a work order he approves on the phone; "status of the boss work" (core/studio)
        from core import studio
        studio_said = studio.handle(raw)
    except Exception as error:  # noqa: BLE001
        print(f"[studio] {type(error).__name__}: {error}")
        studio_said = None
    if studio_said is not None:
        return studio_said
    identity = _identity(raw)       # after a taught phrase (a whole-utterance trigger he taught always wins)
    if identity is not None:
        return identity
    status = _status(raw)
    if status is not None:
        return status
    try:
        from core import localask, place
        if localask.asks_where_am_i(raw):
            return place.where_am_i()               # his live position (phone / PC), as a town; never a guess
    except Exception:  # noqa: BLE001 - unknown: the model answers, as before
        pass
    weather = _weather(raw)
    if weather is not None:
        return weather
    calendar = _calendar(raw)
    if calendar is not None:
        return calendar
    try:
        from core import calintent, ownertime
        calendar = calintent.handle(raw, ownertime.now())
    except Exception:  # noqa: BLE001 - unreadable: the model takes the line, as before
        calendar = None
    if calendar is not None:
        return calendar
    credits = _credits(raw)
    if credits is not None:
        return credits
    checkup = _security(raw)
    if checkup is not None:
        return checkup
    screen = _see_screen(raw)
    if screen is not None:
        return screen
    pairing = _pair_prompt(raw)
    if pairing is not None:
        return pairing
    code = _new_code(raw)
    if code is not None:
        return code
    cooler = _cooler(raw)
    if cooler is not None:
        return cooler
    try:
        from core import partscard, users
        part = partscard.answer(raw, users.title(users.current(), "en") or "sir")   # what one of his own parts IS: facts, not an invented answer
    except Exception:  # noqa: BLE001 - unknown: the model answers, as before
        part = None
    if part is not None:
        return part
    spoken = _speak_only(raw)
    if spoken is not None:
        return spoken
    try:   # "show me my house", "living room camera": the owner's own cameras on the home lab (core/homecam.py)
        from core import homecam
        house = homecam.settings_voice(raw)            # "camera privacy on", "camera alerts on", "follow people"
        if house is None:
            house = homecam.control_voice(raw)         # "turn the camera left", "did anything happen at home?"
        if house is None:
            house = homecam.announce_voice(raw)        # "announce on the living room camera: dinner is ready"
        if house is None:
            house = homecam.voice(raw)
    except Exception as error:  # noqa: BLE001
        house = None
        print(f"[house] voice command failed: {type(error).__name__}: {error}")
    if house is not None:
        return house
    try:   # "open intelligence", "spy mode", "take me to Haiti" (while the globe is open): fixed words, confirmed by the Hub
        from core import worldintel
        world = worldintel.voice(raw)
    except Exception as error:  # noqa: BLE001
        world = None
        print(f"[world] voice command failed: {type(error).__name__}: {error}")
    if world is not None:
        return world
    mission = _mission_command(raw.strip())
    if mission is not None:
        return mission
    reading = _pc_reading(raw.strip())
    if reading is not None:
        return reading
    if _PAIR_MINE.match(raw.strip()):
        # qwen3.5 answered "Pair my phone, please." with a made-up code and no call (2026-11-10): the real skill, always.
        from tools import registry
        result, is_error = registry.run("pair_phone", {"person": "me"})
        text = result if isinstance(result, str) else str(result)
        if is_error:
            return "I couldn't make a pairing code just now, sir."
        # The tool's result is written for the model ("Say it out loud, reading each digit: ..."): speak only the
        # sentence meant for the owner (independent review 2026-11-10).
        quoted = re.search(r'"(Your pairing code is .*?)"', text)
        return quoted.group(1) if quoted else re.split(r"\s+(?:Say it out loud|Always speak)", text)[0]
    # "Watch me minimize Quad" ... "done": the owner shows a PC task once (core/demonstrate).
    from core import demonstrate
    shown = demonstrate.handle(raw)
    if shown is not None:
        return shown
    # A supported guide owns only its explicit offer / acknowledgement turns.
    # Everything else remains ordinary conversation or existing PC/web routing.
    from core import guided_learning
    guided = guided_learning.handle(raw)
    if guided is not None:
        return guided
    if not raw or len(raw) > 200:
        return None
    bare = re.sub(r"[\s,]+(?!\d{3}\b)", " ", raw.replace("’", "'")).strip(" ?.!")
    theirs = _in_their_language(bare)
    if theirs is not None:
        return theirs
    plain = _without_greeting(bare)
    if _CLOCK.fullmatch(plain):
        return f"It's {datetime.now():%I:%M %p}.".replace("It's 0", "It's ")
    if _DATE.fullmatch(plain):
        now = datetime.now()
        return f"It's {now:%A, %B} {now.day}."
    if _WHICH_MODEL.fullmatch(bare) or _OWN_BRAIN.match(bare):
        return _which_model_here(raw)
    if _WHERE_AI.fullmatch(bare):
        where = _where_ai()
        if where is not None:
            return where
    m = _PERCENT_OF.fullmatch(bare)
    if m:
        percent, of = float(m.group(1)), float(m.group(2).replace(",", ""))
        part = percent * of / 100
        if re.search(r"\boff\b", bare, re.I):
            return f"{_number(percent)} percent off {_number(of)} is {_number(part)} off, so {_number(of - part)}."
        return f"{_number(percent)} percent of {_number(of)} is {_number(part)}."
    m = _OHMS.search(bare)
    if m and (re.search(r"\b(?:power|watts?|dissipat\w*|series|parallel|led|diode|resistors|voltage drop)\b", bare, re.I)
              or len(re.findall(r"\d\s*(?:k|m|kilo|mega)?\s*(?:ohms?|Ω|ω)", bare, re.I)) > 1):
        m = None            # power, series/parallel or an LED is not one current: the model works it out (review 2026-11-10)
    if m:
        # "What's 12 volts across 470 ohms, in milliamps?": qwen3.5 said 13.2 then 25.3 mA (it is 25.53). Ohm's law exactly.
        volts = float(m.group("v")) * (1000 if (m.group("vk") or "").lower().startswith("k") else 1)
        ohms = float(m.group("r")) * {"k": 1e3, "m": 1e6}.get((m.group("rk") or "").lower()[:1], 1)
        if ohms > 0:
            amps = volts / ohms
            if amps < 1e-3:
                return (f"{_number(volts)} volts across {_number(ohms)} ohms is {amps * 1e6:.1f} microamps, sir "
                        f"(Ohm's law: current is voltage divided by resistance).")
            if amps < 1:
                return (f"{_number(volts)} volts across {_number(ohms)} ohms is {amps * 1000:.1f} milliamps, sir "
                        f"(Ohm's law: current is voltage divided by resistance).")
            return f"{_number(volts)} volts across {_number(ohms)} ohms is {amps:.2f} amps, sir (Ohm's law: current is voltage divided by resistance)."
    from core import pcbridge
    on_pc = pcbridge.local_pc()
    if on_pc and _CAPABILITIES.fullmatch(bare) and re.search(r"\b(?:pc|computer|master|mastered)\b", bare, re.I):
        from tools import pcskills
        return pcskills.capability_summary()
    if on_pc and _HOGS.search(bare) and not re.search(r"\b(?:how (?:do|can|should) i|why does|should i)\b", bare, re.I):
        return memory_hogs()
    if on_pc and _asks_whats_busy(bare):
        return whats_busy()
    # "what are your skills at the moment?", "where is the gaming coach?": read from the loaded skills (core/selfcard),
    # never from the model's idea of itself.
    from core import selfcard
    known = selfcard.answer(bare)
    if known is not None:
        return known
    if on_pc:
        volume = _volume_command(raw)
        if volume is not None:
            return volume
        if _TIMER_CANCEL.search(raw):
            from tools import life
            which = _TIMER_CANCEL.search(raw).group(1)
            return life.cancel_timer("all" if re.search(r"\ball\b|\bevery", raw, re.I) else which)
        if _TIMER_LIST.search(raw) and not _TIMER_SET.search(raw):
            from tools import life
            return life.list_timers()
        if _TIMER_SET.search(raw):
            seconds = duration(raw)
            if 1 <= seconds <= 86400:
                from tools import system
                return system.set_timer(seconds, _label(raw))
            return None
        if _FOCUS_STOP.search(raw):
            from tools import life
            return life.focus_mode("stop")
        if _FOCUS_START.search(raw):
            from tools import life
            m = re.search(r"(\d+)\s*-?\s*min", raw)
            return life.focus_mode("start", int(m.group(1)) if m else 25)
        if _FOCUS_STATUS.search(raw):
            from tools import life
            return life.focus_mode("status")
    if _WEEK.search(raw):
        from tools import life
        return life.weekly_report()
    if _ACHIEVE.search(raw):
        from tools import life
        return life.my_achievements()
    if _COUNTDOWNS.search(raw):
        from tools import life
        return life.list_countdowns()
    m = _DAYS.search(raw)
    if m:
        from tools import life
        return life.days_until(m.group(1))   # None (-> the brain) when it isn't one of their countdowns
    return None
