"""Verified workflows -> bounded local methods -> local AI -> authorized cloud plan."""
import os
import re
import time
import threading
import uuid

from core import pcactions, pcbridge, pcledger, pcmethods, pcplanner, progress

_last = threading.local()


# Public website commands are deliberately resolved before application lookup:
# “open GitHub” must not launch a locally installed Git client, and “open
# YouTube” must not depend on a Start-menu shortcut being present.
SITES = {
    "youtube": ("https://www.youtube.com/", "https://www.youtube.com/results?search_query={q}"),
    "google": ("https://www.google.com/", "https://www.google.com/search?q={q}"),
    "bing": ("https://www.bing.com/", "https://www.bing.com/search?q={q}"),
    "duckduckgo": ("https://duckduckgo.com/", "https://duckduckgo.com/?q={q}"),
    "wikipedia": ("https://www.wikipedia.org/", "https://en.wikipedia.org/w/index.php?search={q}"),
    "github": ("https://github.com/", "https://github.com/search?q={q}"),
}
_SITE = r"(you\s?(?:tube|to|too)|google|bing|duck\s?duck\s?go|wikipedia|github)(?:\.com|\.org)?"
_SITE_ALIASES = {"you to": "youtube", "you too": "youtube"}
_SEMANTIC_FOREGROUND = {"search_foreground_browser", "scroll_foreground"}
# With a browser in front, "find my shadow world project and launch it", "show me the storage that i have on my pc" and
# "bring up the thing I use to draw" were searched in the browser (gateway eval 2026-11-10). The owner's own things and
# the PC are not web content: those go on to the understanding layer.
_PC_THING = re.compile(r"^(?:my|mine|our|the thing|the app|the program|the one)\b|\b(?:on|in|of|from) (?:my|this|the) "
                       r"(?:pc|computer|laptop|machine|desktop|drive)\b|\b(?:project|folder|files?|drive|storage|disk|space|"
                       r"apps?|programs?|downloads|documents)\b", re.I)
_NAVIGATION_LANGUAGE = re.compile(r"\b(?:scroll|page|article|feed|comments?|higher|lower|up|down|top|bottom|start|end|more|next|previous)\b", re.I)
_BROWSER_SEARCH_LANGUAGE = re.compile(r"\b(?:look|find|search|show|pull|bring)\b", re.I)


def _speech_tolerant(text: str) -> str:
    """Repair only common command-word transcription variants before strict routing.

    This is intentionally a small allowlist, not fuzzy matching across every
    word: a misheard action must never turn ordinary conversation into PC input.
    Search terms themselves are left unchanged.
    """
    repairs = {
        r"\b(?:you\s*tube|you\s*to|you\s*too|utube)\b": "youtube",
        r"\b(?:opem|oppen|opeen)\b": "open",
        r"\b(?:cloze|closs|clos)\b": "close",
        r"\b(?:scrol|scrolll|sroll)\b": "scroll",
        r"\b(?:serch|seach)\b": "search",
        r"\b(?:fynd|finnd)\b": "find",
    }
    for pattern, replacement in repairs.items():
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def _semantic_foreground_plan(task: str, cancelled) -> dict | None:
    """Use the local model only as a narrow language adapter for active-page control.

    The model's output is still schema-validated and then reduced to two
    read-safe interaction types. It cannot turn a vague utterance into close,
    install, typing, click, shell, or cloud work. A terse search is considered
    only while a browser is actually foreground; navigation phrases can target
    another foreground app.
    """
    browser = pcactions.foreground_browser()
    if not (_NAVIGATION_LANGUAGE.search(task) or (browser and _BROWSER_SEARCH_LANGUAGE.search(task))):
        return None
    from core import pcplanner
    plan = pcplanner.local_plan(
        "Interpret this as one foreground-only navigation request if possible. "
        "Allowed tools are only search_foreground_browser or scroll_foreground. "
        "Use search_foreground_browser only if the owner is asking to find/search content in the browser already in front. "
        "Use scroll_foreground for moving the page/app up, down, top, or bottom. "
        "Otherwise return unsupported. Request: " + task, cancelled)
    if not plan:
        return None
    try:
        pcactions.validate(plan)
    except (ValueError, TypeError):
        return None
    steps = plan.get("steps") or []
    if not steps or any(step.get("tool") not in _SEMANTIC_FOREGROUND for step in steps):
        return None
    if any(step["tool"] == "search_foreground_browser" for step in steps) and not pcactions.foreground_browser():
        return None
    return plan


def _semantic_foreground_candidate(task: str) -> bool:
    return bool(_NAVIGATION_LANGUAGE.search(task) or
                (pcactions.foreground_browser() and _BROWSER_SEARCH_LANGUAGE.search(task)))


def _site_key(text: str) -> str:
    spoken = re.sub(r"\s+", " ", text.casefold()).strip()
    return _SITE_ALIASES.get(spoken, re.sub(r"\s+", "", spoken))


def _site_plan(text: str):
    """Deterministic, public-site commands.  They open only an ordinary HTTPS URL."""
    from urllib.parse import quote_plus

    ask = r"(?:look\s+for|look\s+up|search\s+for|search|find(?:\s+me)?|show\s+me)"  # "find me X" searches for X, not "me X"
    match = re.fullmatch(r"(?:open|go\s+to|launch|visit)\s+" + _SITE + r"\s*,?\s*(?:and|then)\s+(play|watch)\s+(.{1,300})", text, re.I)
    if match and _site_key(match[1]) == "youtube":
        return {"steps": [{"tool": "browse_open", "arguments": {"site": "youtube", "query": match[3].strip()}}]}
    match = re.fullmatch(r"(?:play|watch)\s+(.{1,300}?)\s+on\s+you\s?(?:tube|to|too)", text, re.I)
    if match:
        return {"steps": [{"tool": "browse_open", "arguments": {"site": "youtube", "query": match[1].strip()}}]}
    match = re.fullmatch(r"(?:open|go\s+to|launch|visit|pull\s+up|bring\s+up|take\s+me\s+to|fire\s+up|load)\s+(?:the\s+)?" + _SITE, text, re.I)
    if match:
        return {"steps": [{"tool": "open_url", "arguments": {"url": SITES[_site_key(match[1])][0]}}]}
    match = re.fullmatch(r"(?:open|go\s+to|launch|visit|pull\s+up|bring\s+up|take\s+me\s+to|fire\s+up|load)\s+" + _SITE + r"(?:\s*,?\s*(?:and|then)?\s*)" + ask + r"\s+(.{1,300})", text, re.I)
    if match:
        site, query = _site_key(match[1]), match[2].strip()
    else:
        match = re.fullmatch(r"(?:search|look\s+up|find)\s+" + _SITE + r"\s+for\s+(.{1,300})", text, re.I)
        if match:
            site, query = _site_key(match[1]), match[2].strip()
        else:
            match = re.fullmatch(ask + r"\s+(.{1,300}?)\s+(?:on|in|using|with)\s+" + _SITE, text, re.I)
            site, query = (_site_key(match[2]), match[1].strip()) if match else (None, None)
    if site and query:
        return {"steps": [{"tool": "open_url", "arguments": {"url": SITES[site][1].format(q=quote_plus(query))}}]}
    match = re.fullmatch(r"(?:close|quit|exit|dismiss|hide|get\s+rid\s+of)\s+(?:the\s+)?" + _SITE + r"(?:\s+(?:tab|page|site|window))?", text, re.I)
    if match:
        return {"steps": [{"tool": "close_site_tab", "arguments": {"site": _site_key(match[1])}}]}
    return None


def _volume_plan(t):
    """'volume up', 'put the volume down by 50 percent', 'lower the volume by 20', 'turn it up', 'volume to 40%'."""
    amount = r"(?:\s+a\s+(?:bit|little))?(?:\s+by\s+(\d{1,3})(?:\s*(?:%|percent|points?))?)?"
    up = r"(?:up|louder|higher)"
    down = r"(?:down|quieter|lower|softer)"
    match = re.fullmatch(r"(?:put|turn|set|make|bring|take)\s+(?:the\s+)?(?:volume|sound)\s+(?:up\s+|down\s+)?to\s+(\d{1,3})(?:\s*(?:%|percent))?", t, re.I)
    if match and 0 <= int(match[1]) <= 100:
        return {"steps": [{"tool": "set_volume", "arguments": {"percent": int(match[1])}}]}
    for sign, word, verbs in ((1, up, r"(?:raise|increase|turn up|bring up|put up)"), (-1, down, r"(?:lower|decrease|reduce|turn down|bring down|put down)")):
        patterns = (r"(?:(?:put|turn|make|bring|take)\s+)?(?:the\s+)?(?:volume|sound)\s+" + word + amount,
                    r"(?:put|turn|make|bring|take)\s+(?:it|this|that)\s+" + word + amount,
                    verbs + r"\s+(?:the\s+)?(?:volume|sound)" + amount,
                    r"(?:make\s+(?:it|this)\s+)?" + word.replace("(?:", "(?:") + r"(?:\s+a\s+(?:bit|little))?")
        for pattern in patterns:
            match = re.fullmatch(pattern, t, re.I)
            if match:
                number = match.group(1) if match.re.groups else None
                delta = min(100, int(number)) if number else 10
                return {"steps": [{"tool": "change_volume", "arguments": {"delta": sign * delta}}]}
    return None


# Words that point at something said before, never an app's name: the app resolver matched "it" to "git cmd" and "them" to "steam".
_PRONOUN_OBJECTS = frozenset("it that this them those these one ones something anything stuff things him her there here".split())


def _only_pronouns(name: str) -> bool:
    """'it', 'them up', 'that again': words that point back, plus filler, and nothing that could name an app."""
    words = [w for w in re.sub(r"[^a-z ]", " ", (name or "").casefold()).split()
             if w not in ("up", "again", "now", "please", "too", "already", "quickly", "for", "me", "real", "quick")]
    return bool(words) and all(w in _PRONOUN_OBJECTS for w in words)


def simple_plan(task, cached=True):
    t = _speech_tolerant(task.strip())
    t = re.sub(r"^(?:(?:hey\s+)?jarvis[,\s]+|please\s+)", "", t, flags=re.I)
    t = re.sub(r"^(?:(?:yes|yeah|yep|okay|ok|sure|alright|all right|go ahead)[,.!]?\s+)+(?:sir[,.]?\s+)?", "", t, flags=re.I)
    t = re.sub(r"^(?:(?:hey\s+)?jarvis[,\s]+|please\s+)", "", t, flags=re.I)
    if not re.match(r"open\s+https?://", t, re.I):
        t = t.rstrip(".!?")
    t = re.sub(r"\s+(?:on|from)\s+(?:my|the)\s+(?:pc|computer|desktop)$", "", t, flags=re.I)
    # Conversational status after the request (“I have it open”) is not part
    # of the search topic.  Keeping it made the deterministic route miss and
    # let the research/tutor listing route speak a long metadata dump instead.
    t = re.sub(r"(?:[.!?]\s*|\s+)(?:i(?:'ve| have) (?:it|youtube) open|it(?:'s| is) open)$", "", t, flags=re.I)
    ordinal = r"(first|1st|top|second|2nd|third|3rd|fourth|4th|fifth|5th|one|two|three|four|five|[1-8])"
    match = (re.fullmatch(r"(?:open|show|play)\s+(?:the\s+)?" + ordinal + r"\s+(?:one|result|link|video|source|page)", t, re.I)
             or re.fullmatch(r"(?:open|show|play)\s+(?:number|result|video|link|source)\s+" + ordinal, t, re.I))
    if match:
        from core.webbrowse import ORDINALS
        word = match[1].lower()
        return {"steps": [{"tool": "browse_followup", "arguments": {"number": int(word) if word.isdigit() else ORDINALS[word], "mode": "open"}}]}
    match = (re.fullmatch(r"(?:read|tell)\s+me\s+(?:about\s+)?(?:the\s+)?" + ordinal + r"\s+(?:one|result|link|source|page)", t, re.I)
             or re.fullmatch(r"what\s+(?:did|does)\s+(?:the\s+)?" + ordinal + r"\s+(?:one|result|link|source|page)\s+say", t, re.I))
    if match:
        from core.webbrowse import ORDINALS
        word = match[1].lower()
        return {"steps": [{"tool": "browse_followup", "arguments": {"number": int(word) if word.isdigit() else ORDINALS[word], "mode": "read"}}]}
    thing = r"(?:the\s+|this\s+|that\s+|my\s+)?(?:video|music|song|track|playlist|media|audio|playback|youtube|it)?"
    for command, words in (("pause", r"(?:pause|hold|stop playing)"), ("play", r"(?:resume|unpause|continue playing|keep playing|play)"),
                           ("next", r"(?:skip|next|play the next|go to the next)"), ("previous", r"(?:previous|go back to the previous|play the previous|last)")):
        pattern = words + r"\s+" + thing
        if command == "next":
            pattern = r"(?:skip(?:\s+(?:this|the|that)\s+(?:video|song|track))?|next\s+(?:video|song|track)|play the next\s*(?:video|song|track)?|go to the next\s*(?:video|song|track)?)"
        if command == "previous":
            pattern = r"(?:previous\s+(?:video|song|track)|go back to the previous\s*(?:video|song|track)?|play the previous\s*(?:video|song|track)?)"
        if command == "play":
            pattern = r"(?:resume|unpause)\s*" + thing + r"|(?:continue|keep) playing|play\s+(?:the\s+|this\s+|that\s+)?(?:video|music|song|track)"
        if command == "pause":
            pattern = r"(?:pause|hold)\s*" + thing + r"|stop playing"
        if re.fullmatch(pattern, t, re.I):
            return {"steps": [{"tool": "media_command", "arguments": {"command": command}}]}
    from core import mediastate
    seek = mediastate.parse_seek(t)
    if seek:
        return {"steps": [{"tool": "media_command", "arguments": {"command": f"{seek[0]}:{seek[1]}"}}]}
    ranked = r"(?:the\s+)?(?:first|top|best|most relevant)\s+(?:search\s+)?(?:result|link|video|page)"
    match = (re.fullmatch(r"(?:search|look up|find)\s+(google|bing|duckduckgo|youtube)\s+for\s+(.{1,300}?)\s+and\s+(?:open|play|show|click)\s+" + ranked, t, re.I)
             or re.fullmatch(r"(?:search|look up|find)\s+(.{1,300}?)\s+(?:on|in|with)\s+(google|bing|duckduckgo|youtube)\s+and\s+(?:open|play|show|click)\s+" + ranked, t, re.I)
             or re.fullmatch(r"(?:open|play|show)\s+" + ranked + r"\s+(?:for|of|on)\s+(.{1,300}?)\s+(?:on|in|from)\s+(google|bing|duckduckgo|youtube)", t, re.I))
    if match:
        a, b = match[1], match[2]
        site, query = (a, b) if a.lower() in ("google", "bing", "duckduckgo", "youtube") else (b, a)
        return {"steps": [{"tool": "browse_open", "arguments": {"site": site.lower(), "query": query.strip()}}]}
    match = re.fullmatch(r"(?:browse|surf)\s+(?:the\s+)?(?:web|internet)\s+(?:for|about|on)\s+(.{1,500})", t, re.I) or         re.fullmatch(r"(?:research|find\s+sources\s+(?:for|on|about))\s+(.{1,500}?)\s+(?:in|using|with)\s+(?:the\s+)?browser", t, re.I)
    if match:
        return {"steps": [{"tool": "browse_research", "arguments": {"site": "bing", "query": match[1].strip()}}]}
    match = re.fullmatch(r"(?:research|find\s+(?:information|sources))\s+(?:about|on|for)\s+(.{1,500})", t, re.I)
    if match:
        return {"steps": [{"tool": "research_web", "arguments": {"query": match[1]}}]}
    match = re.fullmatch(r"(?:read|inspect|summarize)\s+(https?://\S+)(?:\s+(?:about|for)\s+(.{1,500}))?", t, re.I)
    if match:
        return {"steps": [{"tool": "read_web_page", "arguments": {"url": match[1], "question": match[2] or "main topic"}}]}
    if re.fullmatch(r"find\s+(?:the\s+)?official\s+blender\s+rigging\s+documentation(?:\s+and\s+open\s+it)?", t, re.I):
        return {"steps": [{"tool": "open_url", "arguments": {"url": "https://docs.blender.org/manual/en/latest/animation/armatures/index.html"}}]}
    site_plan = _site_plan(t)
    if site_plan:
        return site_plan
    match = re.fullmatch(r"(?:open|launch|start|run|pull\s+up|bring\s+up|fire\s+up|boot\s+up|load)\s+(?:the\s+)?([\w .+-]{1,100})", t, re.I)
    if match and _only_pronouns(match[1]):
        return None               # "open it" / "open it up" name no app: the resolver once matched "it" to "git cmd" (live 2026-11-15)
    if match:
        from tools.apps import resolve_app
        app = resolve_app(match[1])
        if app:
            return {"steps": [{"tool": "open_app", "arguments": {"app": app}}]}
    parts = re.split(r"\s+and\s+", t, flags=re.I)
    if 1 < len(parts) <= 4 and all(re.match(r"(?:open|launch|start) ", part, re.I) for part in parts):
        plans = [simple_plan(part) for part in parts]
        if all(plans):
            return {"steps": [step for p in plans for step in p["steps"]]}
    match = re.fullmatch(r"(install|uninstall)\s+([\w .+-]{1,100})", t, re.I)
    if match:
        return {"steps": [{"tool": match[1].lower() + "_program", "arguments": {"name": match[2]}}]}
    match = re.fullmatch(r"open\s+(https?://\S+)", t, re.I)
    if match:
        return {"steps": [{"tool": "open_url", "arguments": {"url": match[1]}}]}
    # Minimize is never a close (live 2026-11-08: "Minimize Claude" closed Claude). "hide" minimizes too.
    if re.fullmatch(r"(?:minimi[sz]e|hide|shrink)\s+(?:it|this|the current app|this app|this window|this thing|everything)", t, re.I):
        return {"steps": [{"tool": "minimize_foreground", "arguments": {}}]}
    match = re.fullmatch(r"(?:minimi[sz]e|hide|shrink)\s+(?:the\s+)?([\w .+-]{1,100}?)(?:\s+(?:app|window))?", t, re.I)
    if match:
        from tools.apps import resolve_app
        app = resolve_app(match[1])
        if app:
            return {"steps": [{"tool": "minimize_app", "arguments": {"app": app}}]}
    if re.fullmatch(r"(?:close|quit|exit|dismiss|turn off|shut down|get rid of|remove|shut)\s+(?:it|this|the current app|this app|this window|this thing)|(?:get|take)\s+(?:it|this)\s+(?:off|out of)(?:\s+(?:my|the))?\s*screen", t, re.I):
        return {"steps": [{"tool": "close_foreground", "arguments": {}}]}
    if re.fullmatch(r"(?:close|quit|exit|dismiss|hide|turn off|get rid of)\s+(?:this|the)?\s*(?:website|web ?page|page|tab|site)", t, re.I):
        return {"steps": [{"tool": "close_browser_tab", "arguments": {}}]}
    scroll = re.fullmatch(r"(?:(?:scroll|go|move|page|take\s+me)(?:\s+all\s+the\s+way)?\s+(up|down)(?:\s+(?:a bit|a little|one page|some more))?|(?:scroll|go|jump|take\s+me)(?:\s+all\s+the\s+way)?(?:\s+to\s+the)?\s+(top|bottom|start|end)|(?:show\s+me\s+)?more|keep\s+going|(?:a\s+little\s+)?(higher|lower))", t, re.I)
    if scroll:
        direction = (scroll.group(1) or scroll.group(2) or scroll.group(3) or "down").lower()
        direction = {"start": "top", "end": "bottom", "higher": "up", "lower": "down"}.get(direction, direction)
        return {"steps": [{"tool": "scroll_foreground", "arguments": {"direction": direction}}]}
    match = re.fullmatch(r"(?:look\s+(?:up|for)|search(?:\s+(?:for|up))?|find(?:\s+me)?|pull\s+up|show\s+me|bring\s+up|google)\s+(.{2,300})", t, re.I)
    if match and not _PC_THING.search(match[1]) and pcactions.foreground_browser():
        return {"steps": [{"tool": "search_foreground_browser", "arguments": {"query": match[1].strip()}}]}
    close_words = r"(?:close|quit|exit|dismiss|turn off|shut down|get rid of)"
    match = re.fullmatch(close_words + r"\s+(?:the\s+)?([\w .+-]{1,100}?)(?:\s+(?:app|window))?", t, re.I)
    if not match:
        match = re.fullmatch(r"i\s+(?:do not|don't)\s+want\s+to\s+see\s+(?:the\s+)?([\w .+-]{1,100}?)(?:\s+(?:app|window))?", t, re.I)
    if not match:
        match = re.fullmatch(r"(?:make|take)\s+(?:the\s+)?([\w .+-]{1,100}?)\s+(?:go away|disappear)", t, re.I)
    if match:
        from tools.apps import resolve_app
        target = match[1]
        if re.fullmatch(r"(?:all\s+(?:of\s+)?(?:the\s+|my\s+)?)?(?:files|file explorer|file explorer windows|explorer windows|folders)", target, re.I):
            target = "file explorer"       # "close all files": his File Explorer windows (holdout h034), never FileSyncHelper
        app = resolve_app(target)
        if app:
            return {"steps": [{"tool": "close_app", "arguments": {"app": app}}]}
    match = re.fullmatch(r"focus\s+([\w .+-]{1,100})", t, re.I)
    if match:
        from tools.apps import resolve_app
        app = resolve_app(match[1])
        if app and app in pcactions.APPS:
            return {"steps": [{"tool": "focus_app", "arguments": {"process": pcactions.APPS[app]}}]}
    vol = _volume_plan(t)
    if vol:
        return vol
    match = re.fullmatch(r"(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})(?:\s*(?:%|percent))?", t, re.I)
    if match and 0 <= int(match[1]) <= 100:
        return {"steps": [{"tool": "set_volume", "arguments": {"percent": int(match[1])}}]}
    if t.casefold() in ("pc status", "computer status", "check my pc", "check my pc status"):
        return {"steps": [{"tool": "pc_status", "arguments": {}}]}
    if re.fullmatch(r"(?:what|which|list|show)(?:'s| is| are)?(?: me)?(?: (?:the|all|my))? ?(?:windows?|apps?|programs?)(?: (?:that |which )?(?:are|is))? ?(?:open|running|up)(?: (?:on|in) (?:my |the )?(?:pc|computer|desktop))?(?: right now)?\??", t, re.I):
        return {"steps": [{"tool": "list_windows", "arguments": {}}]}
    if re.fullmatch(r"(?:blender status|check blender)", t, re.I):
        return {"steps": [{"tool": "inspect_blender", "arguments": {}}]}
    if re.fullmatch(r"(?:list|show) (?:the |my )?blender (?:scene )?objects", t, re.I):
        return {"steps": [{"tool": "list_scene_objects", "arguments": {}}]}
    if re.fullmatch(r"what(?:'s| is) (?:in )?(?:my |the )?blender(?: scene)?", t, re.I):
        return {"steps": [{"tool": "blender_status", "arguments": {}}]}
    if not cached:        # core.pchelp's fallback: the fixed wordings only, never a remembered model guess
        return None
    try:                  # any other wording of an open / close / switch-to request: pcfast asked the local model (core.pcsemantic) just before
        from core import pcsemantic
        return pcsemantic.cached(task)
    except Exception:  # noqa: BLE001
        return None


def is_request(task):
    # Only intercept actual PC commands, not ordinary conversational mentions of computers.
    return bool(simple_plan(task) or pcledger.workflow(task) or re.match(r"run (?:saved )?pc workflow ", task, re.I) or re.search(
        r"\b(?:open|launch|start|focus|close|configure|set up|setup|export|render|organize|control|fix)\b.*\b(?:my pc|the pc|computer|desktop|blender|notepad)\b", task, re.I))


_STEP_WORDS = {
    "open_app": "open {app}", "focus_app": "bring {process} to the front", "close_app": "close {app}", "close_foreground": "close the app in front",
    "close_browser_tab": "close the browser tab", "close_site_tab": "close the {site} tab", "open_url": "open {url}",
    "set_volume": "set the volume to {percent}%", "change_volume": "change the volume by {delta}", "get_volume": "read the volume",
    "pc_status": "check the PC status", "list_windows": "list the open windows", "process_status": "check whether {process} is running",
    "search_foreground_browser": "search the browser in front for '{query}'", "scroll_foreground": "scroll {direction}",
    "browser_search": "search {site} for '{query}'", "browse_research": "research '{query}' on {site}", "browse_open": "open '{query}' on {site}",
    "research_web": "research '{query}'", "read_web_page": "read {url}", "media_command": "media: {command}",
    "install_program": "install the program '{name}'", "uninstall_program": "uninstall the program '{name}'",
    "use_app": "in {app}: {goal}", "render_preview": "render a preview to {path}", "export_glb": "export GLB to {path}",
    "blender_status": "check Blender", "list_scene_objects": "list the Blender scene", "inspect_blender": "inspect Blender",
    "find_project": "find the project '{name}'", "open_path": "open {path}", "find_files": "list {pattern} files in {folder}",
    "type_text": "type '{text}' into {app}", "save_document": "save the {app} document as {path}", "read_screen": "read the screen",
    "run_terminal": "run in the terminal: {command}", "investigate_slowness": "check why the PC is slow",
}


def plan_words(plan) -> str:
    """The plan in plain words, so a review request on the phone or by voice says WHAT would run, not just that something would.
    'Plan: 1) uninstall the program 'ripgrep'. 2) ...' with sensitive steps marked; '' when there is nothing to say."""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not steps:
        return ""
    parts = []
    for i, s in enumerate(steps, 1):
        tool, args = s.get("tool", "?"), s.get("arguments") or {}
        try:
            words = _STEP_WORDS[tool].format(**{k: str(v)[:80] for k, v in args.items()})
        except (KeyError, IndexError):
            words = tool.replace("_", " ") + (" " + ", ".join(f"{k}={str(v)[:80]}" for k, v in args.items()) if args else "")
        from core import authz
        sensitive = authz.effect(s) in (authz.IRREVERSIBLE, authz.EXTERNAL)      # what asks first, by effect
        parts.append(f"{i}) {words}{' (sensitive)' if sensitive else ''}")
    return "Plan: " + " ".join(p + "." for p in parts)


_APP_ARGS = ("app", "process")
_SPOKEN_WORDS = {"use_app": "{goal} in {app}", "uninstall_program": "uninstall {name}", "install_program": "install {name}",
                 "media_command": "{command} the media"}


def spoken_plan(plan) -> str:
    """The plan the way a person says it: "close Paint", "bring Edge to the front, then close the browser tab". No
    numbering and no "Plan:" (owner 2026-11-08: the step-by-step readout "makes him sound like a robot")."""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not steps:
        return ""
    from core import targets
    parts = []
    for s in steps:
        tool, args = s.get("tool", "?"), dict(s.get("arguments") or {})
        for key in _APP_ARGS:
            if isinstance(args.get(key), str):
                args[key] = targets.display(os.path.splitext(args[key])[0] if key == "process" else args[key])
        try:
            parts.append(_SPOKEN_WORDS.get(tool, _STEP_WORDS.get(tool, "")).format(**{k: str(v)[:80] for k, v in args.items()})
                         or tool.replace("_", " "))
        except (KeyError, IndexError):
            parts.append(tool.replace("_", " "))
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", then " + parts[-1]


def result(state, detail, **fields):
    # The numbered plan stays on the record (phone screen, logs) but is no longer read out in front of every question.
    if state == "needs_review" and fields.get("plan") is not None:
        fields.setdefault("plan_words", plan_words(fields["plan"]))
    value = {"state": state, "detail": detail, **fields}
    _last.value = value
    return value


def last_result():
    return getattr(_last, "value", None)


def clear_result():
    _last.value = None


_NAMED_TARGET_TOOLS = {"close_app": "close", "minimize_app": "minimize"}
_CLOSE_TO_MINIMIZE = {"close_app": "minimize_app", "close_foreground": "minimize_foreground"}
_MINIMIZE_WORDS = re.compile(r"\b(?:minimi[sz]e|hide|shrink)\b", re.I)


def close_needs_close_word(plan, task: str, route: str):
    """-> (plan, result). A close step runs only when the owner asked for a close, on every route. Live 2026-11-08 (after
    Mark I): "Hey Jarvis. Hey Jarvis. Minimize Claude." closed Claude: a route outside the gateway planned close_app and
    nothing checked the verb. He said minimize -> the step becomes the minimize he asked for (reversible, runs). No close
    word and no minimize word -> nothing runs and he is asked. `result` is None when the plan may go on."""
    from core import authz, targets
    steps = list((plan or {}).get("steps") or [])
    wrong = [i for i, s in enumerate(steps) if s.get("tool") in _CLOSE_TO_MINIMIZE and not authz.said_to_do(s, task)]
    if not wrong:
        return plan, None
    if _MINIMIZE_WORDS.search(task or ""):
        fixed = [dict(s, tool=_CLOSE_TO_MINIMIZE[s["tool"]]) if i in wrong else s for i, s in enumerate(steps)]
        return {**plan, "steps": fixed}, None
    step = steps[wrong[0]]
    app = str((step.get("arguments") or {}).get("app") or "")
    what = targets.display(app) if app else "the app in front"
    rid = pcledger.create("plan", {"task": task, "plan": plan, "route": route, "cloud_usd": 0})
    return plan, result("needs_review", f"Should I close {what}?", approval_id=rid, plan=plan, task=task, route=route, cloud_usd=0,
                        guarded=True, why="you did not ask me to close anything")


def unnamed_target_guard(plan, task: str, route: str, observed=()):
    """Never close or minimize an app the owner did not name, whichever route produced the plan. Live 2026-11-08: "Close chat
    TVT" closed Paint (a model's guess on a route that skipped review). A step aimed at an app the owner's words do not name
    runs nothing; the owner is asked about the open app his words most resemble (core.targets), and his "yes" runs exactly
    that corrected plan (the same pending-plan record any review uses). None: every target was named, nothing to ask."""
    from core import authz, targets
    steps = list((plan or {}).get("steps") or [])
    unnamed = [i for i, s in enumerate(steps) if s.get("tool") in _NAMED_TARGET_TOOLS
               and not authz.in_scope(s, task, list(observed))]
    if not unnamed:
        return None
    corrected = [dict(s, arguments=dict(s.get("arguments") or {})) for s in steps]
    offers = []
    for i in unnamed:
        guess = targets.suggest(task)
        if guess is None:
            offers = []
            break
        corrected[i]["arguments"]["app"] = guess["app"]
        offers.append((_NAMED_TARGET_TOOLS[steps[i]["tool"]], str((steps[i].get("arguments") or {}).get("app") or ""),
                       str(guess["app"])))
    if not offers:
        # Nothing open resembles his words: ask about the planned app BY NAME (his yes is consent to exactly that app).
        verb = _NAMED_TARGET_TOOLS[steps[unnamed[0]]["tool"]]
        wrong = targets.display(str((steps[unnamed[0]].get("arguments") or {}).get("app") or "that"))
        rid = pcledger.create("plan", {"task": task, "plan": plan, "route": route, "cloud_usd": 0})
        return result("needs_review", f"Should I {verb} {wrong}?", approval_id=rid, plan=plan, task=task, route=route, cloud_usd=0,
                      guarded=True, why=f"you did not name {wrong}")
    plan_fixed = {**plan, "steps": corrected}
    rid = pcledger.create("plan", {"task": task, "plan": plan_fixed, "route": route, "cloud_usd": 0})
    verb, wrong, right = offers[0]
    return result("needs_review", f"Did you mean {targets.display(right)}?", approval_id=rid, plan=plan_fixed, task=task,
                  route=route, cloud_usd=0, guarded=True, why=f"you did not name {targets.display(wrong)}")


def direct_owner_close(plan, route: str) -> bool:
    """Allow only an authenticated deterministic close-only plan without review."""
    return (route == "local_automation" and isinstance(plan, dict) and bool(plan.get("steps"))
            and all(step.get("tool") in {"close_app", "close_foreground", "close_browser_tab", "close_site_tab"}
                    for step in plan["steps"]))


def direct_owner_foreground(plan, route: str) -> bool:
    """An owner-authenticated local-language parse may only navigate the active UI."""
    return (route == "local_foreground_ai" and isinstance(plan, dict) and bool(plan.get("steps"))
            and all(step.get("tool") in _SEMANTIC_FOREGROUND for step in plan["steps"]))


def run(task, cancelled=lambda: False, *, plan=None, route=None, observed=(), on_start=None, review=False):
    """Run a PC task. With `plan` (the gateway's typed steps, route "gateway") the planning stages are skipped and
    core.authz decides whether to ask; without it the legacy routing (stored plan, workflow, regex, local planner) decides.
    on_start() is called once the plan is really going to run (past every review question): a spoken "Right away, sir"
    must never come before "Should I close it?". review: ask once even where core.authz would not (the steps were heard in a
    spoken list: core.pcsteps); his yes to exactly this plan runs it as for any plan."""
    started = time.monotonic()
    pcbridge.require_owner()
    if not isinstance(task, str) or not task.strip() or len(task) > 4000:
        return result("stopped", "Provide a PC task of at most 4000 characters.")
    from core import heard
    task = heard.normalise(task.strip())       # recognition slips put right once, for every route (core.heard)
    from core.words import after_correction
    task = after_correction(task).strip()      # "close X. Nevermind. Minimize X.": the withdrawn clause neither plans nor authorizes
    if not task:
        return result("cancelled", "Okay, I'll leave it.")
    routing_started = time.monotonic()
    from core import hud
    own = hud.companion_command(task) or hud.own_screen(task)   # relayed from the phone: Jarvis's own screens, not an app to launch
    if own:
        return result("completed", own, task=task, route="jarvis_screen", evidence=[{"verified": True, "detail": own}])
    stored = pcledger.prepared(task)
    approved, cost = False, 0
    observed = list(observed)
    given = plan
    route = route or "local_automation"
    if given is not None:
        if stored and stored["data"].get("plan") == given:
            approved = bool(pcledger.consume("plan", stored["data"]))     # the owner's yes to exactly this plan
        plan, stored = given, None
    elif stored:
        data = stored["data"]
        plan, route, cost = data["plan"], data["route"], data.get("cloud_usd", 0)
        approved = bool(pcledger.consume("plan", data))
        if not approved:
            return result("needs_review", f"Should I {spoken_plan(plan) or 'go on'}?", approval_id=stored["id"], plan=plan, task=task, route=route, cloud_usd=cost)
    else:
        workflow = pcledger.workflow(re.sub(r"^run (?:saved )?pc workflow ", "", task, flags=re.I))
        plan = workflow["plan"] if workflow else simple_plan(task)
        if workflow:
            route = "saved_workflow"
        if plan is None:
            planning_started = time.monotonic()
            semantic_candidate = _semantic_foreground_candidate(task)
            semantic = _semantic_foreground_plan(task, cancelled) if semantic_candidate else None
            if semantic is not None:
                plan, route = semantic, "local_foreground_ai"
            elif semantic_candidate:
                return result("stopped", "I couldn't map that to a safe foreground action, so I left the app alone.")
            else:
                progress.step("planning locally; no paid click loop")
                pcplanner.clear_local_diagnostic()
                from core import pcstate
                plan = pcplanner.local_plan(task, cancelled, context=pcstate.describe())   # the planner sees what is open right now
                route = "local_ai"
            if plan is None and not cancelled():
                cloud = pcplanner.cloud_plan(task, cancelled)
                if cloud["state"] != "planned":
                    diagnostic = pcplanner.local_diagnostic()
                    if not cloud.get("approval_id"):
                        # No plan and no question to put to him: plain words and a next step, not the planner's policy
                        # sentence ("Local-only inference is enabled; paid planning is disabled...", core.pchelp). The
                        # diagnostic stays in the result for the PC page.
                        from core import pchelp, users
                        return result("stopped", pchelp.no_plan(task, users.title(), [f"local: {diagnostic.get('code', '')}"]
                                                                 if diagnostic.get("category") == "model_unavailable" else ()),
                                      task=task, local_planning=diagnostic)
                    cloud["detail"] = diagnostic["detail"] + " " + cloud["detail"].replace(
                        "Local planning could not solve this. ", "").replace("Local planning could not produce a supported plan. ", "")
                    return result(**cloud, task=task, local_planning=diagnostic)
                plan, cost, route = cloud["plan"], cloud["cloud_usd"], "cloud_ai"
            pcledger.latency("pc_planning", planning_started, route=route)
        if cancelled():
            return result("cancelled", "Stopped before any step executed.")
    try:
        pcactions.validate(plan)
    except (ValueError, TypeError) as error:
        return result("stopped", str(error))
    if not approved and demonstrated(task, plan):
        approved = True              # the owner showed exactly these steps himself and said yes to a try-run (core/demonstrate)
    if not approved:
        plan, guarded = close_needs_close_word(plan, task, route)
        if guarded is not None:
            return guarded
        guarded = unnamed_target_guard(plan, task, route, observed)
        if guarded is not None:
            return guarded
    direct_close = direct_owner_close(plan, route)
    direct_foreground = direct_owner_foreground(plan, route)
    if route == "gateway":
        # One policy, by effect and by the scope of the owner's instruction (core.authz), whatever produced the plan.
        from core import approvals, authz, users
        titles = []
        try:
            from tools import pcops
            titles = [w.get("title", "") for w in pcops.windows()]
        except Exception:  # noqa: BLE001
            pass
        verdict = authz.decide(plan["steps"], task, autonomous=approvals.pc_autonomous(users.current()), approved=approved,
                               observed=observed, observed_titles=titles)
        requires_review = verdict.ask
        why = "; ".join(verdict.reasons)
        explicit_yes = approved          # the owner said yes to exactly this plan (not only standing autonomy)
        if review and not explicit_yes:
            requires_review = True
            why = why or "steps heard in a spoken list: asked once before any of them runs"
        elif not verdict.ask:
            approved = True              # the owner's standing authorization covers every step; the typed actions' own "sensitive" gate is this policy's
    else:
        requires_review = (route in ("local_ai", "cloud_ai")
                           or any(pcactions.SCHEMAS[s["tool"]][1] for s in plan["steps"]))
        why = ""
    if not approved and requires_review and not direct_close and not direct_foreground:
        rid = pcledger.create("plan", {"task": task, "plan": plan, "route": route, "cloud_usd": cost})
        what = spoken_plan(plan)
        return result("needs_review", f"Should I {what}?" if what else "Should I go on?",
                      approval_id=rid, plan=plan, task=task, route=route, cloud_usd=cost, why=why)
    pcledger.latency("pc_routing", routing_started, route=route)
    execution_id = uuid.uuid4().hex
    from core import pcagent, pcstate

    def replan(task_, done, failed, reason, state_text):
        if route == "gateway":
            from core import gateway
            return gateway.replan(task_, done, failed, reason, state_text, cancelled)
        return pcplanner.replan(task_, done, failed, reason, cancelled, context=state_text)
    allowed_fn = reauthorize = None
    if route == "gateway":
        from core import authz
        allowed_fn = lambda step, task_, reviewed, seen: authz.allowed_recovery(step, task_, list(observed) + list(seen))  # noqa: E731
        if not explicit_yes:
            # a step filled in by an observation ('<found>' -> a path) is judged again; one that now asks stops for review
            reauthorize = lambda step: authz.effect(step) not in (authz.IRREVERSIBLE, authz.EXTERNAL)  # noqa: E731
    if on_start is not None:
        try:
            on_start()
        except Exception:  # noqa: BLE001 - an acknowledgment never stops the work
            pass
    budget = pcagent.turn_budget(plan)           # a plain one-app verb has a wall-clock budget for the whole turn
    at = None if budget is None else time.monotonic() + budget
    with pcmethods.turn_deadline(at):
        outcome = pcagent.pursue(task, plan, execute=pcmethods.execute, replan=replan, observe=pcstate.describe,
                                 approved=approved or direct_close or direct_foreground, cancelled=cancelled, execution_id=execution_id,
                                 progress=progress.step, allowed_fn=allowed_fn, reauthorize=reauthorize,
                                 deadline=at, settle=_settled, dialog_open=_dialog_open)
    evidence, state = outcome["evidence"], outcome["state"]
    if state == "needs_review":          # a recovery wants more than the owner approved: the same review as any model plan
        rid = pcledger.create("plan", {"task": task, "plan": outcome["proposed"], "route": route if route == "gateway" else "local_ai", "cloud_usd": cost})
        return result("needs_review", (compose_detail(evidence, state) + " " + outcome["detail"]).strip(),
                      approval_id=rid, plan=outcome["proposed"], task=task, route=route, cloud_usd=cost)
    if state == "needs_choice":          # an observation found several things: the owner picks, the rest of the plan waits
        return result("needs_choice", " ".join(e["detail"] for e in evidence), task=task, route=route, choices=outcome["choices"],
                      remaining=outcome["remaining"], evidence=evidence)
    run_data = {"state": state, "verified": state == "completed", "plan": outcome["plan"], "evidence": evidence, "route": route,
                "cloud_usd": cost, "execution_id": execution_id, "recoveries": outcome["recoveries"]}
    rid = pcledger.create("run", run_data, state)
    detail = compose_detail(evidence, state) or "No steps executed."
    if state == "completed" and len([e for e in evidence if e.get("verified")]) > 2 and all(e.get("verified") for e in evidence):
        # Owner 2026-11-08: no step-by-step readout he did not ask for. Live 2026-11-09 a learned 4-click task answered
        # "Clicked Seven in Calculator. Clicked Plus in Calculator. ..." — a finished job is one line; the steps stay in the run.
        detail = done_line(plan)
    if state == "completed" and route in ("local_ai", "gateway") and not outcome["recoveries"] and learnable(task, plan):
        try:                             # next time this exact sentence runs the reviewed plan straight away, no model needed
            pcledger.save_workflow(rid, task[:80], task)      # learned silently; saying so every time was noise
        except (ValueError, KeyError, TypeError):
            pass
    if state == "completed" and route == "demonstration" and not outcome["recoveries"]:
        try:                             # the owner showed it, said yes, and the try-run worked: his phrase now runs it
            pcledger.save_workflow(rid, task[:80], task, demonstrated=True)
        except (ValueError, KeyError, TypeError):
            pass
    pcledger.latency("pc_total", started, route=route, state=state)
    _note_outcome(route, state, run_data["verified"], outcome)
    return result(state, detail, run_id=rid, execution_id=execution_id, route=route, verified=run_data["verified"], cloud_usd=cost,
                  stop_reason=outcome.get("stop_reason", ""))


def compose_detail(evidence: list, state: str) -> str:
    """What he hears, composed ONCE from the final state of the run, not from every attempt on the way. A step that failed and
    was then re-planned or settled is history (it stays in the ledger run, not in his ears): when the run completed, only what
    was verified is said; when it stopped, what was verified plus the LAST failure, never a string of failed attempts that
    contradict each other (live 2026-11-13: "I couldn't close Notepad. ... I could not see the window. Closed notepad.")."""
    rows = [e for e in evidence if str(e.get("detail") or "").strip()]
    said = [str(e["detail"]).strip() for e in rows if e.get("verified")]
    if state != "completed":
        # The REAL reason is the last failure the app itself gave. The run's own bookkeeping rows ("I ran out of time", "the only
        # idea was to repeat what failed", "the alternative plan failed validation") come after it and never replace it: a launch
        # still starting must not be spoken as "I gave up". Only the out-of-time line follows a real reason; with no real reason
        # the last bookkeeping row stands alone.
        reasons = [str(e["detail"]).strip() for e in rows if not e.get("verified") and not e.get("meta")]
        notes = [e for e in rows if not e.get("verified") and e.get("meta")]
        if reasons:
            said.append(reasons[-1])
            said.extend(str(e["detail"]).strip() for e in notes[-1:] if e.get("error_kind") == "out_of_time")
        elif notes:
            said.append(str(notes[-1]["detail"]).strip())
    out = []
    for line in said:
        if not out or out[-1] != line:
            out.append(line)
    return " ".join(out)


def _settled(step: dict):
    """A close or an open that reported failure but whose goal the PC now shows reached (the window went a moment late, or
    appeared a moment late): the verified outcome, else None. A close is settled by the app's visible windows (the same matcher
    the close uses: any window it counts is still there). An open is settled only by a window that is the app's OWN
    (apps.own_windows): "Crossword - Google Chrome" is not Word, so an unverified launch stays unverified."""
    tool = step.get("tool")
    if tool not in ("close_app", "open_app"):
        return None
    from tools import apps
    canonical = apps.resolve_app(str((step.get("arguments") or {}).get("app", "")))
    if not canonical:
        return None
    for _ in range(4):
        shown = bool(apps.visible_own_windows(canonical) if tool == "open_app" else apps.visible_windows_for_app(canonical))
        if shown == (tool == "open_app"):
            return {"verified": True, "detail": f"{'Opened' if tool == 'open_app' else 'Closed'} {canonical}.", "app": canonical, "settled": True}
        time.sleep(.25)
    return None


def _dialog_open(step: dict) -> bool:
    """Is a dialog (a save prompt) really in the way of this step's app? Only then may a failed close look inside the app."""
    try:
        from core import saveprompt
        app = str((step.get("arguments") or {}).get("app", ""))
        return bool(app and saveprompt.buttons(app))
    except Exception:  # noqa: BLE001 - unreadable: no dialog was detected
        return False


def _note_outcome(route, state, verified, outcome) -> None:
    """Put the run's route, state and failing tool on the turn-timing row (core.turntrace): never breaks a turn."""
    try:
        from core import turntrace
        ran = [s.get("tool", "?") for s in (outcome.get("plan") or {}).get("steps", [])]
        bad = next((e for e in outcome["evidence"] if e.get("step") and not e.get("verified")), None)
        failed = ran[bad["step"] - 1] if bad and 0 < bad["step"] <= len(ran) else None
        turntrace.outcome(route, state, verified, ran, failed)
    except Exception:  # noqa: BLE001
        pass


def done_line(plan: dict) -> str:
    """'Done: 4 steps in Calculator.' for a finished plan of several verified steps."""
    from core import targets
    from core.words import plural
    steps = (plan or {}).get("steps") or []
    apps = []
    for s in steps:
        app = str((s.get("arguments") or {}).get("app") or "").strip()
        if app and app not in apps:
            apps.append(app)
    where = (" in " + " and ".join(targets.display(a) for a in apps[:2])) if apps else ""
    return f"Done: {plural(len(steps), 'step')}{where}."


def demonstrated(task: str, plan: dict) -> bool:
    """Is this plan exactly the saved workflow the owner demonstrated for this phrase?"""
    try:
        wf = pcledger.workflow(task)
    except Exception:  # noqa: BLE001
        return False
    return bool(wf and wf.get("demonstrated") and wf.get("plan") == plan)


_ONLY_ON_SCREEN = {"open_app", "focus_app", "minimize_app", "minimize_foreground"}


def _asks_more(task: str, steps: list) -> bool:
    """Does the sentence say more than 'get this app on screen' ('open paint', 'minimize claude', 'bring claude forward')?
    Words left after the apps' names and the open / show / minimize / filler words mean it asked for something else too."""
    from core import targets
    names = set()
    for s in steps:
        for value in (s.get("arguments") or {}).values():
            names |= set(re.findall(r"[a-z0-9]+", str(value).casefold()))
    words = re.findall(r"[a-z0-9']+", task.casefold())
    extra = {"want", "i", "i'm", "let's", "lets", "pull", "put", "get", "bring", "forward", "back", "nevermind", "never", "mind",
             "minimise", "maximize", "restore", "foreground", "top", "over"}
    return any(w not in targets._FILLER and w not in names and w not in extra for w in words)


def learnable(task: str, plan: dict) -> bool:
    """A verified model plan becomes a saved workflow for that exact sentence only when every argument was said in the
    sentence (nothing inferred from what happened to be open) and no step is sensitive: a replay must mean the same thing
    tomorrow. "open spotify and set the volume to 30" qualifies; "close the game" (close_app apex) does not."""
    low = task.casefold()
    steps = plan.get("steps") or []
    # A plan that only gets the app on screen does not do what a sentence asking for work INSIDE it said. Live 2026-11-08:
    # "Click on Autonomous Blender Learning in ChatGPT" ran open_app, verified, and was saved, so it would only ever open.
    from core.gateway import _DOING
    if _DOING.search(task) and steps and all(s.get("tool") in _ONLY_ON_SCREEN for s in steps):
        return False
    if steps and all(s.get("tool") in _ONLY_ON_SCREEN for s in steps) and _asks_more(task, steps):
        # Any verb, not only the doing-words above: live 2026-11-09 "In Calculator, multiply eight by three." ran open_app
        # only, verified, and was saved, so that sentence would only ever open Calculator.
        return False
    for step in steps:
        if pcactions.SCHEMAS.get(step.get("tool"), ({}, True))[1]:
            return False
        for value in step.get("arguments", {}).values():
            if str(value).strip() and str(value).strip().casefold() not in low:
                return False
    return bool(steps)
