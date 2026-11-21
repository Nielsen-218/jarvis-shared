"""What a sentence asks for from the live internet, in ordinary words: a web search ("google this", "look it up",
"search online for", "find the latest"), YouTube tutorials, a recall of saved research without going online, or
"learn this" to keep what was just found. Deterministic, so a clear request never depends on the small local model
choosing the right skill, and bare phrases or local requests ("search my notes") never launch a search.

Phone-app and wake-word prefixes are stripped first, like core/quick.py does."""
import re

from core import webanswer

_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*", re.I)
_WAKE = re.compile(r"^\W*(?:(?:hey|ok|okay|yo|hi)\W+)?(?:jarvis|charvis|jervis|travis|javis)\W*", re.I)
_LEAD = re.compile(r"^(?:(?:ok|okay|so|um|uh|please|hey|jarvis|can you|could you|would you|will you|i want you to|"
                   r"i need you to|go ahead and|quick(?:ly)?)[\s,]+)*", re.I)

# Things kept on this PC: never a web search.
_LOCAL = re.compile(r"\b(?:my|our) (?:notes?|lists?|reminders?|memor(?:y|ies)|library|calendar|files?|wardrobe|photos?|"
                    r"recordings?|journal|vault|shopping|pantry|budget|expenses?|workouts?|screen|desktop)\b|\b(?:in|from) (?:the|my|your) "
                    r"(?:library|notes|vault|memory|journal)\b|\bon (?:my|the) (?:screen|pc|computer|desktop)\b", re.I)
_SEARCH = re.compile(r"\b(?:google|bing|duckduckgo|search(?:ing)? (?:the )?(?:internet|web|online|net)|"
                     r"search (?:online |the web |the internet )?for|do an? (?:internet |web |online |quick )?search|"
                     r"look (?:this|that|it|him|her|them|\w+(?: \w+)?) up(?: online)?|look up|"
                     r"find (?:me |out )?(?:the |what the |what'?s the )?(?:latest|newest|current|most recent)|"
                     r"what(?:'s| is| are) the (?:latest|newest|most recent|current)|check online|browse the (?:web|internet)|"
                     r"on the (?:internet|web)|from the (?:internet|web)|web search|internet search|search this up|"
                     r"search (?:that|this|it) (?:up|online)|research (?:this|that|it|how|what|why|which|the|a|an|\w+ online))\b", re.I)
# "could you scour the internet for whatever might spoil my match" (Whisper also hears "scour" as "scatter"): a sweep of the
# web FOR something. Not "I like to surf the web at night", "go through the internet history" or "the net worth sheet", and
# not one he says he will do himself ("I'll scan the internet for it after dinner"): review 2026-11-11.
_SCOUR = re.compile(r"\b(?:scour|scower|scatter|scan|comb|crawl|trawl|surf|sweep|dig through|go through|look through|search through)"
                    r" (?:the )?(?:web|internet|net) (?:for|to (?:find|see|check|look|learn|get)|and (?:find|see|look|tell|check|get))\b", re.I)
_SOMEONE = re.compile(r"\b(?:i|i'll|i'm|i'd|i've|we|we'll|we're|he|she|they|you'll)\b", re.I)
_ASKS_JARVIS = re.compile(r"\b(?:can|could|would|will) you\b|\b(?:need|want|like) you to\b|\bplease\b|\bjarvis\b", re.I)
# A named web site with a finding verb is a web search too: "look for X on GitHub", "search reddit for Y" (the owner's words
# gate 2026-11-11: typed into the browser in front instead of answered). "open github" / "show me github" are not.
_SITE = r"(?:github|git hub|reddit|wikipedia|stack ?overflow)(?! desktop\b)"
_ON_SITE = re.compile(r"\b(?:on|in|at|from|through|across|using) (?:the )?" + _SITE + r"\b|\bsearch(?:ing)? " + _SITE + r"\b", re.I)
_SITE_FIND = re.compile(r"\b(?:find|look(?:ing)? for|look up|search(?:ing)?|dig up|research)\b", re.I)
# ...but a step on the page itself (a click, a scroll, a button or section, instructions read from the screen) is PC work
_ON_PAGE = re.compile(r"\b(?:click|press|tap|scroll|button|section|tab|menu|icon|sidebar|instructions?|steps?|sign in|log in|"
                      r"star|fork|clone|clear|delete)\b", re.I)


# "Go ahead and check for Halal food, Haitian food, Caribbean food." (holdout h055, 2026-11-19): asked of Jarvis at the start
# of the line, about something out in the world. Never PC work ("check for updates", "look for the save button", "check for
# viruses"), never something of his ("look for my keys"), never one he will do himself ("I'll check for it later").
_CHECK_FOR = re.compile(r"^(?:(?:ok|okay|so|um|uh|please|hey|jarvis|yes|yeah|now|and|then|go ahead and|can you|could you|would you|"
                        r"will you|i want you to|i need you to)[\s,]+)*(?:check|look|search) (?:around |online )?for\s+(?P<what>\S.*)", re.I)
_CHECK_PC = re.compile(r"\b(?:updates?|upgrades?|drivers?|virus(?:es)?|malware|threats?|errors?|warnings?|files?|folders?|downloads?|"
                       r"windows?|apps?|programs?|tabs?|screen|cpu|gpu|memory|ram|disk|drive|storage|space|process(?:es)?|"
                       r"notifications?|messages?|e-?mails?|mail|texts?|calls?|problems?|issues?|bugs?|crash(?:es)?|leaks?|"
                       r"button|icon|menu|settings?|option|pc|computer|laptop|phone|device|network|wi-?fi|connection|signal|lag|"
                       r"mesh(?:es)?|vert(?:ex|ices|s)|geometry|normals?|faces?|edges?|objects?|models?|scenes?|rigs?|bones?|"
                       r"textures?|materials?|code|scripts?|logs?|data|patterns?|values?|cells?|rows?|columns?|lines?|text|words?|"
                       r"typos?|spelling|mistakes?|duplicates?|changes?|conflicts?|jarvis|anything|everything|something|nothing|"
                       r"my|our|me|mine|it|that|this|them|those|these|him|her)\b", re.I)


def _check_for(t) -> bool:
    m = _CHECK_FOR.match(t)
    if not m:
        return False
    from core import pcsemantic
    what = m.group("what")
    return not (_CHECK_PC.search(what) or _ON_PAGE.search(what) or pcsemantic.mentioned_apps(what))


def _scour(t) -> bool:
    m = _SCOUR.search(t)
    if not m:
        return False
    before = re.split(r"[.!?;]", t[:m.start()])[-1]
    return not (_SOMEONE.search(before) and not _ASKS_JARVIS.search(before))


def _new_search(t) -> bool:
    """The wider web-search wording (2026-11-11), never over PC work on a page."""
    return (_scour(t) or bool(_ON_SITE.search(t) and _SITE_FIND.search(t))) and not _ON_PAGE.search(t)
_YOUTUBE = re.compile(r"\b(?:youtube|video tutorials?|tutorial videos?|videos? (?:on|about|for))\b", re.I)
_FIND = re.compile(r"\b(?:find|look for|search|get me|show me|recommend|suggest|dig up|pull up)\b", re.I)
_RECALL = re.compile(r"\b(?:what (?:did|have) (?:you|we) (?:find|found|learn|learned|save|saved|research|look(?:ed)? up)|"
                     r"what (?:do you|did you) (?:remember|know) (?:from|about) (?:your|the|that|our) (?:research|search)|"
                     r"from (?:your|the|our) (?:saved |earlier |previous |last )?(?:research|search|findings|sources)|"
                     r"(?:your|the) saved (?:research|sources|findings)|"
                     r"without (?:searching|the internet|going online|a search)|(?:offline|saved) (?:notes|research))\b", re.I)
_LEARN = re.compile(r"^(?:learn (?:this|that|it)|save (?:this|that|the|those|these|what you found|it)(?: (?:finding|findings|result|"
                    r"results|research|sources?|info|information))?(?: for later)?|keep (?:this|that|those|it) for later|"
                    r"remember what you (?:just )?found|add (?:that|this|it) to (?:the|your) library|"
                    r"save (?:this|that|it) (?:to|in) (?:the|your) library|study (?:this|that)|learn from (?:this|that))\b", re.I)
# Questions about JARVIS's own Blender/rigging skills or lessons are about him: never a saved-research recall.
_OWN_SKILL = re.compile(r"\b(?:rigging|rigged|rig|blender|3d|character)\b.*\b(?:skills?|abilit(?:y|ies)|lessons?|tests?|good at|able to|can you)\b|"
                        r"\b(?:your|you) (?:\w+ )?(?:rigging|blender)\b|\bwhat have you learned\b.*\b(?:rigging|blender)\b", re.I)
_TEACHING = re.compile(r"\b(?:when|if|whenever) i say\b", re.I)          # a taught command, not a saved finding
_COUNT = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "a few": 3, "some": 3, "several": 4, "a couple of": 2}


def _bare(text) -> str:
    return _WAKE.sub("", _CHANNEL.sub("", text or "")).strip()


_OPEN_ENGINE = re.compile(r"^(?:open|launch|start|go (?:on|to)|pull up|bring up|load)\s+(?:up\s+)?(?:the\s+)?(?:google|bing|duckduckgo|browser)\W*$", re.I)
_ASKS_LEARNED = re.compile(r"^(?:(?:hey |ok |okay |please )?jarvis[, ]+|please )?what(?: new)?(?: skills?)? (?:did|have) you learn(?:ed)?"
                           r"(?: (?:lately|recently|so far|today|this (?:week|month)))?[ ?.!]*$|^(?:which|what) (?:new )?skills "
                           r"(?:did|have) you learn(?:ed)?\b", re.I)


# The news, and "do your research": the live internet, never the model's memory. Live 2026-11-14 (qwen3.5:9b): "What's on
# the news about this? People have to bunker down. What's going on?" and "Do your research and tell me what the news is
# saying about that." were answered "I've checked the major news feeds and there is no credible report... misinformation"
# and "I'm pulling the latest headlines right now" with no search run. Only when he ASKS for it: "I heard on the news that
# gas is going up" tells something, and "that's good news" is a remark.
_NEWS_ASK = re.compile(
    r"\bwhat(?:'s| is| are|s)? (?:on |in )?(?:the |today'?s |this morning'?s |tonight'?s )?(?:latest |breaking |big |top )?(?:news|headlines)\b|"
    r"\bwhat (?:is|are|does|do) (?:the )?(?:news|media|press|reports?|headlines) (?:saying|say|reporting|report)\b|"
    r"\b(?:any|is there(?: any)?|are there(?: any)?) (?:big |breaking |new |more )?(?:news|headlines)\b(?! for me\b)|"
    r"\b(?:tell|give|read|get|show|check|pull up|bring up|catch) (?:me )?(?:up on )?(?:the |today'?s |some )?(?:latest |top |breaking )?"
    r"(?:news|headlines)\b|"
    r"\b(?:latest|breaking|today'?s|top) (?:news|headlines)\b|"
    r"\bwhat(?:'s| is) (?:going on|happening) (?:in|with|around) (?:the )?(?:world|country|news|us|usa|united states)\b", re.I)
_RESEARCH_ASK = re.compile(
    r"\bdo (?:your|some|a little|a bit of|the|more)? ?research\b|\bresearch (?:on|about|into)\b", re.I)
# Mentions, not asks: a news app or channel, a document's headlines, "later", "thanks", or him reporting what he heard.
_NEWS_VETO = re.compile(
    r"\b(?:news|headlines?)\s+(?:app|channel|widget|tab|site|feed|anchor|station|ticker)\b|"
    r"\b(?:headlines?|news|articles?)\s+(?:of|in|from)\s+(?:this|my|the|that)\s+(?:document|article|page|file|email|doc|pdf|book|report)\b|"
    r"\b(?:later|thanks|thank you|when i|claude|codex)\b|"
    r"\b(?:on|in|of|at) (?:this|the|that|my) (?:page|screen|site|tab|window|feed)\b|"
    r"\b(?:(?:pr|prs|build|builds|deploy|deployment|ci|service|server|mission)s? (?:status|results?|is|was|are|passed|failed)|status of)\b|"
    r"\bjarvis(?! mark)\b|\b(?:my|our|the) (?:pr|prs|build|builds|deploy|repo|mission)\b|\b(?:my|our) pc\b", re.I)   # claude/codex: it is the coding agent being told
_REPORTED = re.compile(r"^\W*(?:(?:and|so|but|well)\W+)*(?:i|we)\s+(?:heard|saw|read|got|watched|just (?:heard|saw|read|watched))\b", re.I)


_RESEARCH_REQUEST = re.compile(
    r"^\W*(?:(?:and|so|ok|okay|well|hey|jarvis|please|yes|yeah|no|actually)\W+)*"
    r"(?:(?:can|could|would|will) you |i (?:want|need|would like|'d like) you to |go ahead and |please |let'?s )?"
    r"(?:(?:do|run|start|get) (?:(?:your|some|a little|a bit of|the|more|a) )?research\b|research (?:on|about|into)\b)", re.I)


def _news_sentence(sentence: str) -> bool:
    """One sentence that really asks (not one that mentions): news asks as before, research only as a request to him."""
    if _NEWS_VETO.search(sentence) or _REPORTED.match(sentence):
        return False
    if _NEWS_ASK.search(sentence):
        return True
    return bool(_RESEARCH_ASK.search(sentence) and _RESEARCH_REQUEST.match(sentence.replace("’", "'")))


def _asks(t: str) -> bool:
    """Some sentence of the line really asks for the news or for research (not one that mentions news)."""
    return any(_news_sentence(s) for s in re.split(r"(?<=[.!?])\s+", t) if s.strip())
# The words of the asking itself, taken off to leave the topic.
_NEWS_WORDS = re.compile(
    r"\b(?:(?:so|and|ok|okay|well|hey|jarvis|please)\W+)*"
    r"(?:(?:can|could|would|will) you |i (?:want|need|would like|'d like) you to |go ahead and )?"
    r"(?:do (?:your|some|a little|a bit of|the|more)? ?research(?: (?:on|about|into))?|research (?:on|about|into)|"
    r"check (?:on )?(?:the )?news(?: (?:for|about|on))?|"
    r"(?:and )?(?:tell|give|read|get|show) me|(?:and )?let me know|(?:do|did) you (?:have|got) (?:any )?|"
    r"what(?:'s| is| are|s)? (?:on |in )?(?:the |today'?s )?(?:latest |breaking |big |top )?(?:news|headlines)(?: (?:is|are))?(?: (?:saying|reporting))?|"
    r"what (?:is|are|does|do) (?:the )?(?:news|media|press|reports?|headlines) (?:saying|say|reporting|report)|"
    r"(?:any|is there(?: any)?|are there(?: any)?) (?:big |breaking |new |more )?(?:news|headlines)|"
    r"(?:the )?(?:latest|breaking|today'?s|top) (?:news|headlines)|what(?:'s| is) (?:going on|happening)(?: (?:in|with|around) (?:the )?(?:world|country|news)(?! (?:cup|series|war|bank|record|health|economy|trade|order)))?|"
    r"(?:pull|bring) up (?:the )?(?:news|headlines)|(?:the )?(?:news|headlines)(?= (?:about|on)\b))\b",
    re.I)
_ABOUT = re.compile(r"^\W*(?:about|on|regarding|concerning|for|with|of|around|in|from)\b\W*", re.I)
_POINTS = re.compile(r"^\W*(?:this|that|it|me|us|these|those|them|all (?:this|that)|the (?:situation|thing|story|matter))?\W*$", re.I)
# How he reports what he heard: taken off the front of a topic ("I see that people say the government is saying to...").
_HEARSAY = re.compile(r"^\W*(?:(?:and|so|well|because|but)\W+)*(?:i (?:see|saw|hear|heard|read|keep (?:seeing|hearing)|was told)|"
                      r"(?:people|they|everyone|everybody|folks|some people) (?:say|said|are saying|keep saying|claim|think)|"
                      r"(?:apparently|supposedly|somebody said|someone said))\b(?: (?:that|how|where))?\W*", re.I)
_FILLER_TAIL = re.compile(r"\W*(?:and|or) (?:stuff|things|whatever|all that|so on)(?: like that)?\W*$|\W*(?:or something|you know)\W*$", re.I)


def asks_news(text) -> bool:
    """Does he ask for the news or for research on the live internet (as opposed to mentioning news)?"""
    t = _bare(text)
    return bool(t) and not _LOCAL.search(t) and _asks(t)


_INTERJECTION = re.compile(r"^\W*(?:(?:yes|yeah|yep|no|nope|okay|ok|sure|right|well|so|and|actually|first of all|sir|jarvis)\b\W*)+", re.I)


def _topic(sentence: str) -> str:
    sentence = _INTERJECTION.sub("", sentence)
    for _ in range(3):
        sentence = _HEARSAY.sub("", sentence)
    return _FILLER_TAIL.sub("", sentence).strip(" .!?,:;-\"'")


_PC_COMMAND = re.compile(r"^\W*(?:(?:please|jarvis|hey|ok|okay|can you|could you)\W+)*(?:open|close|launch|start|stop|turn|set|play|pause|"
                         r"press|click|type|run|switch|mute|unmute|lock|shut|restart|kill|move|drag|save|delete|remind|add|remember)\b", re.I)


_SENSITIVE = re.compile(r"\b(?:password|passcode|pin|secret|token|key|ssn|social security|diagnos|illness|sick|medic|doctor|lab results?|"
                        r"results came|account|balance|salary|debt|lawyer|divorce|pregnan|therap)\w*", re.I)


_FAMILY = re.compile(r"\b(?:i|we|we're|we'd|we'll|he|she|he's|she's|his|her|hers|mom|mum|mama|mommy|dad|papa|daddy|grandma|grandpa|wife|husband|"
                     r"sister|brother|son|daughter|kids?|baby|girlfriend|boyfriend|friend|neighbou?r|boss|code|street|address|surgery|house|home)\b", re.I)
_CHATTER = re.compile(r"^\W*(?:(?:thanks|thank you|good (?:morning|night|evening|afternoon)|hello|hi|hey|ok|okay|yes|no|nope|yeah|"
                      r"it's|that's|jarvis|sir)\b[\s,.!]*)+$|\bit's (?:cold|hot|late|early)\b|\b(?:thanks|thank you|that's all)\b", re.I)


def private_line(text, strict: bool = True) -> bool:
    """A line about HIM or his household or a command to the PC: never a search topic (it would leave the machine).
    strict: also anything that reads as unprompted personal talk (sensitive words, chatter); the topic he names himself in
    an ask is judged without that, so "any news on the doctor strike" still works."""
    from core import intelligence
    t = re.sub(r"\b(?:the )?drive (?:to|from|time|distance)\b", "trip", (text or "").replace("’", "'"), flags=re.I)   # "the drive to Richmond"
    if not t.strip():
        return False
    if strict and (_SENSITIVE.search(t) or _CHATTER.search(t)):
        return True
    if (_PC_COMMAND.match(t) or _FAMILY.search(t) or intelligence.about_them(t)
            or re.search(r"\b(?:i'm|i am|i've|i have|i'll|i'd|i was|i feel|i need|i want|i like|my|mine|myself|our|ours|me)\b", t, re.I)):
        return True
    return household_named(t)


_WORLD = re.compile(r"\b(?:president|prime minister|capital|population|score|champion(?:s|ship)?|released?|invented|founded|born|died|"
                    r"won|wins?|lost|playoffs?|election|stocks?|price of|weather|forecast|earthquake|hurricane|storm|recall|"
                    r"standings|season|tournament|launch(?:ed)?|merger|ceo)\b", re.I)


_NOT_NAMES = {"I", "I'm", "I'd", "I'll", "I've", "Jarvis", "Dad", "Mom", "Mum", "Mama", "Papa", "Grandma", "Grandpa", "RAM", "CPU", "GPU",
               "PC", "TV", "Wi-Fi", "WiFi", "USB", "Windows", "Blender", "Claude", "Codex", "Monday", "Tuesday", "Wednesday", "Thursday",
               "Friday", "Saturday", "Sunday"}


_LOCAL_Q = re.compile(r"\b(?:code|gate|door|dog|cat|house|car|build|builds|deploy|deployment|meeting|calendar|package|screen|balance|account|ram|"
                      r"cpu|gpu|disk|battery|file|files|folder|printer|lights?|thermostat|router|wi-?fi|garage|fridge|oven|mission|task|"
                      r"reminders?|usage|memory|storage|installed|dad|mom|mum|wife|husband|sister|brother|son|daughter|kids?|baby)\b", re.I)
_ASK_LEAD = re.compile(r"^\W*(?:(?:hey |ok |okay |so |and |well |please )?jarvis[, ]+)?(?:(?:so |and |well )*)"
                       r"(?:(?:do|could|can|would) you (?:happen to )?(?:know|tell me|find out|check|look up)|i(?:'m| was| am) wondering|i wonder|"
                       r"tell me|let me know|find out|look up|do you know|could you|can you)\W+", re.I)


_EMPTY_Q = re.compile(r"^(?:what(?:'s| is| was| are)? (?:really |actually |all )?(?:going on|happening|the story|the deal)"
                      r"(?: with (?:that|this|it|all (?:of )?(?:this|that)))?|what happened|what(?:'s| is) (?:that|this) about|"
                      r"(?:is|was|are) (?:that|this|it|they|those) (?:really |actually |all )?(?:true|real|right|correct|accurate|legit)|"
                      r"is (?:that|this|it) (?:really |actually )?(?:happening|a thing)|(?:is|was) (?:that|this) so)$", re.I)
_CLAIM_CUE = re.compile(r"\b(?:people|everyone|everybody|folks|some people|they|officials?|the government|the news|reports?|media|experts?)\b[^.?!]{0,30}"
                        r"\b(?:say|says|saying|said|claim\w*|report\w*|announc\w*|warn\w*)\b", re.I)
_SELF_WORDS = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|me|mine|we|our|ours|us|you|your|yours|yourself|their|theirs|jarvis|alex|alex)\b", re.I)
_HEDGE = re.compile(r"\b(?:i|we)\s+(?:just\s+)?(?:heard|hear|read|saw|see)\b|\bapparently\b|\bsupposedly\b|\brumou?rs?\b|\bthey say\b|"
                    r"\bit(?:'s| is) (?:all )?(?:over )?the news\b", re.I)
# What a claim must be about to be searched for him: a public event or institution, not a person nobody has heard of
# ("Everyone says Jessica from work is quitting", "People are saying Mark is dead" go nowhere).
_PUBLIC = re.compile(r"\b(?:government|president|congress|senate|supreme court|army|military|troops?|navy|war|invasion|missiles?|bomb(?:ing|ed|s)?|"
                     r"election\w*|voters?|tariffs?|sanctions?|treaty|hurricane|tornado|earthquake|wildfire|shutdown|inflation|outbreak|"
                     r"pandemic|vaccine|shooting|protests?|riots?|evacuat\w*|bunker|nasa|fbi|cia|nato|cdc|fda|nuclear|ceasefire|summit|"
                     r"minister|prime minister|attacked|attacks?|struck|strikes?|police|stock market)\b", re.I)
# Never in a query, however public the rest sounds: any digit (house numbers, codes, phones), a street, a place near him, a
# school or shop, a person "named ..."/"... from work", a machine, a game or an app (review 2026-11-15: "the police arrested Tyler",
# "the update crashed", "the attack on the base is op in Apex", "the shooting at Springfield High School" must never be searched).
_NEVER = re.compile(r"\d|\S+@\S+|\b(?:owner|neighbou?rs?|named|called|from work|at work|next door|"
                    r"street|st|lane|ln|road|rd|avenue|ave|dr|blvd|boulevard|circle|trail|"
                    r"springfield|decatur|norfolk|virginia|va|peninsula|"
                    r"school|church|hospital|mall|walmart|neighbou?rhood|apartment|"
                    r"update|patch|game|games|app|apps|driver|drivers|server|launcher|windows|blender|apex|valorant|steam|discord|python|"
                    r"command|bug|bugged|broken|op|overpowered|nerf|titan|legends|fortnite|minecraft|nvidia|amd|intel|chrome|firefox|"
                     r"youtube|netflix|laptop|pc|computer|phone|wifi|router|battery)\b", re.I)
# A claim is searched only when EVERY word of it is a known grammar word, a public-news word or a public place/organisation: a
# closed vocabulary that fails closed. A private name, a relative, a nearby town, a street, a shop, a document, a machine, a game
# or anything else not listed makes the whole claim "not for a search engine" (two reviews 2026-11-15 found, one by one, every
# deny-list gap: lowercase names, "I saw the police outside", "Everyone in the family says", Jacksonville, tylersmith.com ...).
_VOCAB = frozenset((
    # grammar
    "a an the and or but if then than that those of in on at to from by for with without about against into over under after before "
    "during between through is are was were be been being am do does did done has have had will would can could should may might must "
    "not no it its they them there which what when where why how all any some every each more most many much few other another such "
    "only also just very too so as up down out off again still now soon today tonight tomorrow yesterday new old first last next "
    "big large small major own both either neither whole entire "
    # people and institutions in the news
    "everyone everybody people officials official experts expert reports report news media government governments president presidents "
    "congress senate military troops troop army navy war wars invasion missile missiles bomb bombs bombing bombed border town city cities "
    "country countries nation nations world state states federal national global international capital base bases nuclear election "
    "elections vote votes voters minister ministers supreme court police shooting protest protests protesters riot riots hurricane "
    "tornado earthquake wildfire shutdown inflation outbreak pandemic vaccine ceasefire summit treaty sanctions sanction tariff tariffs "
    "bunker stock market economy recession virus airline airlines flights flight strike strikes attack attacks attacked attacking striking bombed "
    # what they do or say
    "want wants wanted plan plans planned planning going gonna say says said saying buy sell food water cash gas oil fuel supplies "
    "price prices rising rise rose fall fell drop dropped drops higher lower ban banned banning bans close closed closing shut open "
    "reopen resign resigned resigning fired signed sign signing announce announced announcing approve approved pass passed passing "
    "block blocked order ordered orders told telling ask asked hiding hide hidden cancel cancelled canceled delay delayed end ended "
    "ending start started starting begin began launch launched hit hits struck killed kill dead died death deaths injured wounded "
    "destroyed destroy invade invaded invading seized seize captured arrested arrest charged charge sentenced convicted raise raised "
    "cut cuts increase increased decrease decreased move moved moving send sent sending deploy deployed deploying declare declared "
    "declaring confirm confirmed confirms deny denied denies refuse refused agree agreed agreement talks talk meet met meeting "
    "evacuate evacuated evacuating evacuation warn warned warning warnings bill bills law laws act policy rule rules claim claims "
    "claimed true false real fake rumor rumors rumour rumours more less likely soon").split())
_PUBLIC_NAMES = frozenset(w.casefold() for w in (
    "Iran Israel Gaza Hamas Hezbollah Houthi Houthis Ukraine Russia China Taiwan Bahrain Syria Iraq Lebanon Yemen Egypt Turkey "
    "Pakistan Korea Japan Europe Asia Africa America American Americans US USA UK Britain British France Germany Canada Mexico "
    "NATO NASA FBI CIA UN EU CDC FDA Pentagon Kremlin Beijing Moscow Tehran Jerusalem Brussels Taliban Afghanistan Venezuela Cuba "
    "Greenland Panama Saudi Arabia Qatar Palestinian Palestinians Israeli Iranian Russian Chinese Ukrainian").split())
# What introduces a reported claim, anchored at the START of the sentence: nothing may sit between the speaker and the verb, and the
# source is never part of the search ("Everyone in the family says ..." and "I heard from Tyler that ..." are not matched).
_HEARSAY_LEAD = re.compile(
    r"^(?:(?:people|everyone|everybody|folks|they|reports?|officials?|experts?|the news|the media)(?: are| is| were| have been| keep)? "
    r"(?:say|says|saying|said|reporting|reports|report|claiming|claim|claims|announcing|announced|warning|warned)(?: that)?"
    r"|(?:i|we)(?: just)? (?:heard|read|saw|hear|see)(?: that)?|apparently|supposedly|rumou?r has it(?: that)?)\s+", re.I)
_DEIXIS = re.compile(r"\b(?:here|this|these|our|your|their|you|jarvis|outside|inside|nearby|near|local|around)\b", re.I)


def _public_words_only(topic: str) -> bool:
    """Every word of the claim is a known grammar, news or public-place word; any digit, symbol or non-ASCII letter fails."""
    if not topic.isascii() or re.search(r"[^A-Za-z' ]", topic):
        return False
    for word in topic.split():
        if "'" in word:
            return False                  # a possessive or a contraction: somebody's thing
        if word.casefold() not in _VOCAB and word.casefold() not in _PUBLIC_NAMES:
            return False
    return True


# What speech puts in front of a sentence ("So, um, so I heard ..."): not part of the claim, never part of a query.
_FILLERS = re.compile(r"^(?:(?:so|well|okay|ok|um+|uh+|erm|er|and|but|yeah|yes|hey|jarvis|like|alright|all right|right|oh|hmm+|you know|actually|look|listen|anyway|basically|honestly)[,.\s]+)+", re.I)


def _topic_before(sentences) -> str:
    """The public claim the sentence before an empty question reports ("People are saying the government wants everyone to bunker
    down and buy food."), as a search; '' when there is none: he said it about himself, his house, his machine or a person, it
    names a word that is not a plain public one, or no one public is quoted (then nothing leaves the machine)."""
    prior = _FILLERS.sub("", (sentences[-1] if sentences else "").strip()).strip().rstrip(".!")
    if not prior or prior.endswith("?"):
        return ""                 # the sentence before is itself a question: its words are not a claim to check
    lead = _HEARSAY_LEAD.match(prior)
    if not lead:
        return ""                 # the source must open the sentence: "I read in an email that ..." is not a public source
    topic = prior[lead.end():].strip()
    if len(topic.split()) < 3 or len(topic.split()) > 22 or not _public_words_only(topic) or _DEIXIS.search(topic):
        return ""
    if (_SELF_WORDS.search(topic) or _LOCAL_Q.search(topic) or _NEVER.search(topic) or private_line(topic)
            or private_line(topic, strict=False) or household_named(topic)):
        return ""                 # belt and braces over the vocabulary
    if not _PUBLIC.search(topic):
        return ""                 # nothing in it is a public event or institution
    return topic + " news"


# "How much room is free?" (his "How much RAM is free?", misheard; live 2026-11-16) went to a web search: a bare "how much X is
# free / left / used" about a PC resource is about HIS machine, never searched.
_MACHINE_Q = re.compile(r"how much (?:free |spare )?(?:room|space|storage|ram|memory|vram|disk|drive|hard drive|ssd|battery)"
                        r"(?: space)?(?: (?:is|do i have|have i got|is there|am i using|is being used|have i))?"
                        r"(?: (?:free|left|available|spare|remaining|open|used|in use))?"
                        r"(?: (?:on|in) (?:my|the|this) (?:pc|computer|laptop|machine|system|drive|disk|ssd|phone))?"
                        r"(?: (?:right now|now|at the moment))?", re.I)


def lookup_query(text) -> str:
    """The question to put to the internet for a line he said: its worldly question sentence, with the asking taken off
    ("Do you know who won..." -> "who won...") and every other sentence (the gate code, an address, a sick relative)
    left out. '' when no sentence is a worldly question that is free of him, his house, his machine and his household:
    then nothing leaves the machine."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", _bare(text).replace("\u2019", "'")) if s.strip()]
    for index in range(len(sentences) - 1, -1, -1):
        s = sentences[index]
        q = _ASK_LEAD.sub("", s)
        q = re.sub(r"[,\s]+(?:jarvis|sir)\W*$", "", q, flags=re.I).strip(" .!?,")
        if _EMPTY_Q.match(_FILLERS.sub("", q)):
            # "What is going on?" / "Is that true?" asks about the sentence before it. Searched alone it finds anything, and the
            # reply then says "I found nothing" about a question never put (live 2026-11-15, the bunker-down line).
            return _topic_before(sentences[:index])
        if len(q.split()) < 3 or not (s.endswith("?") or _ASK_LEAD.match(s)
                                      or re.match(r"(?i)(?:who|what|when|where|which|how|why|is|are|did|does|do)\b", q)):
            continue
        if (re.search(r"(?i)\byou(?:r|rs|rself)?\b", q) or _LOCAL_Q.search(q) or _MACHINE_Q.fullmatch(q.strip(" .!?,"))
                or private_line(q, strict=False)
                or household_named(q)):
            continue
        if worldly_question(q):
            return q
    return ""


def lookup_plan(text, limit: int = 2) -> tuple[list[str], list[str], list[str]]:
    """([searched], [not searched: over the limit], [not searched: about him or his household]) for a line that asks more than one
    worldly thing: the questions in the order he asked them, each split further at "and who/what/...". Only the first `limit` are
    searched; the answer says what it did not search. Nothing is searched when no question is free of him and his house."""
    found, private = [], []
    for s in webanswer.split_sentences(_bare(text).replace("\u2019", "'")):
        for part in webanswer.split_questions(s):
            part = webanswer._FILLER_LEAD.sub("", part)
            q = lookup_query(part + "?" if s.endswith("?") else part)
            if q:
                if q not in found:
                    found.append(q)
            elif _QUESTION_START.match(part):                 # a question that was not searched is always said
                private.append(part.strip(" ?.!,"))
    return found[:limit], found[limit:], private


def lookup_queries(text, limit: int = 2) -> list[str]:
    """The worldly questions to search for a line (the first `limit`, in the order he asked them)."""
    return lookup_plan(text, limit)[0]


def lookup_skipped(text, limit: int = 2) -> str:
    """What the answer must say about the questions of the line that were not searched ('' when all were)."""
    _, over, private = lookup_plan(text, limit)
    return _not_searched(over, private)


def _not_searched(skipped, private=()) -> str:
    notes = []
    if skipped:
        notes.append("he also asked: " + "; ".join(skipped) + ". Those were NOT searched (only the first two are searched at a time): "
                     "tell him so plainly and say he can ask for them next.")
    if private:
        notes.append("he also named: " + "; ".join(private) + ". That was NOT searched: it is about him, his household or his machine "
                     "(or not something the internet can answer) and never goes to a search engine. Tell him so.")
    return "NOTE: " + " ".join(notes) if notes else ""


def worldly_question(text) -> bool:
    """Is this a question about the world (a named person, team or place, or a world-fact word), as against the house,
    the machine, the calendar or the conversation? Only those are looked up on the internet."""
    t = (text or "").strip()
    words = re.findall(r"[A-Za-z][A-Za-z'.-]*", t)
    named = any(w[0].isupper() and w.strip(".'") not in _NOT_NAMES for w in words[1:])
    lowered = bool(re.match(r"(?i)(?:who|what|when|where|which|how)\b", t)) and len(words) >= 4 and not _LOCAL_Q.search(t)
    return bool(named or _WORLD.search(t) or lowered)


def household_named(text) -> bool:
    """Does the line name someone of the household (first name, user id)? Family names never go to a search engine."""
    try:
        from core import users
        names = set()
        for uid, rec in users.all_users().items():
            for n in (uid, (rec or {}).get("name"), (rec or {}).get("display_name")):
                if isinstance(n, str) and len(n.strip()) >= 3:
                    names.add(n.strip().split()[0].casefold())
        names.discard("owner")
        words = set(re.findall(r"[a-z]+", (text or "").casefold().replace("'s", " ")))
        return bool(names & words)
    except Exception:                                           # noqa: BLE001 - unknown household: assume nothing is safe
        return True


def _claim_topic(sentences) -> str:
    """'<what he heard> news' when the LAST sentence is an empty question ("What is going on?", "Is that true?") and the one before
    it is a public claim (see _topic_before); '' otherwise."""
    if not sentences or len(sentences) > 2:
        return ""                 # the claim and its question and nothing else: another sentence may be an order or private
    q = _FILLERS.sub("", _ASK_LEAD.sub("", sentences[-1]))
    q = re.sub(r"[,\s]+(?:jarvis|sir)\W*$", "", q, flags=re.I).strip(" .!?,")
    return _topic_before(sentences[:-1]) if _EMPTY_Q.match(q) else ""


def news_query(text, previous: str = "") -> str:
    """What to search for when he asks for the news or for research. The topic is what remains of the asking sentence
    ("Any news on the election?" -> "election news"); when that only points ("about this", "about that"), the other
    sentences of the same line ("People have to bunker down."), else what he said just before (`previous`). With no
    topic at all it is the day's headlines."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", _LEAD.sub("", _bare(text))) if s.strip()]
    claim = _claim_topic(sentences)
    if claim:
        return claim            # "People are saying X. Is that true?": the news about X
    asking = [s for s in sentences if _news_sentence(s)]
    others = [s for s in sentences if s not in asking and not re.fullmatch(r"\W*what(?:'s| is) (?:going on|happening)\W*", s, re.I)]
    topic = ""
    for s in asking:
        rest = _NEWS_WORDS.sub(" ", s)
        for _ in range(3):
            rest = _ABOUT.sub("", rest.strip())
        rest = re.sub(r"\b(?:the )?(?:news|media|press|headlines) (?:is|are) (?:saying|reporting)\b", " ", rest, flags=re.I)
        rest = re.sub(r"\s+", " ", rest).strip(" .!?,:;-\"'")
        rest = re.sub(r"\b(?:please|thanks|thank you)\b", " ", rest, flags=re.I)
        rest = re.sub(r"\s+", " ", rest).strip(" .!?,:;-\"'")
        if rest and not _POINTS.match(rest) and len(rest) >= 2:
            topic = _topic(rest)
            break
    if not topic and others:
        topic = " ".join(filter(None, (_topic(s) for s in others if not private_line(_topic(s)))))
    if (not topic and previous and not asks_news(previous) and not private_line(_topic(_LEAD.sub("", _bare(previous)))) and not previous.strip().endswith("?")
            and len(previous.split()) >= 3 and not _SENSITIVE.search(previous)):
        topic = _topic(_LEAD.sub("", _bare(previous)))
    topic = re.sub(r"\b(?:today|tonight|right now|at the moment|lately|these days|this (?:morning|week))\b", " ", topic, flags=re.I)
    topic = re.sub(r"\s+", " ", topic).strip(" .!?,:;-\"'")[:160]
    news = any(_NEWS_ASK.search(s) for s in asking)
    if not topic or re.fullmatch(r"(?:the |some |any )?(?:latest |top )?(?:news|headlines)", topic, re.I):
        return "top news headlines today"
    return f"{topic} news" if news and not re.search(r"\bnews\b", topic, re.I) else topic


def kind(text) -> str | None:
    """'search' | 'youtube' | 'recall' | 'learn' | None (ordinary conversation, a local request, a bare phrase)."""
    t = _bare(text)
    if not t or len(t.split()) > 60 or t.count("\n") > 3:
        return None
    if _ASKS_LEARNED.match(t):
        return None                                        # his own learned-skills list (core/learned), not saved research
    body = _LEAD.sub("", t)
    if _LEARN.match(body) and not _TEACHING.search(body):
        return "learn"
    if _OWN_SKILL.search(t) and not _SEARCH.search(t):
        return None
    if _RECALL.search(t):
        return "recall"
    from core import localask
    if localask.subject(t) and not _OWN_SKILL.search(t) and not _SEARCH.search(t):
        return "local"                                      # a question about HERE: searched by town, never by his words (core/localask)
    if _LOCAL.search(t):
        return None
    if _YOUTUBE.search(t) and (_FIND.search(t) or _SEARCH.search(t)):
        return "youtube"
    if _OPEN_ENGINE.match(body):
        return None                                         # "Open Google." / "go on google": the browser, not a question
    if _SEARCH.search(t) or _new_search(t) or _check_for(body):
        return "search"
    if _claim_topic([x.strip() for x in re.split(r"(?<=[.!?])\s+", t) if x.strip()]):
        return "news"                                       # a public claim and "Is that true?": looked up, never answered from memory
    if _asks(t) and not _ON_PAGE.search(t):
        return None if private_line(news_query(t), strict=False) else "news"
    return None


_STRIP = [
    r"^(?:open|launch|go (?:on|to)|pull up)\s+(?:up\s+)?(?:the\s+)?(?:google|bing|duckduckgo|(?:a |the )?browser|(?:microsoft )?edge|chrome|firefox)\s*(?:,|and|then)?\s+(?=(?:search|look|find|google)\b)",
    r"^(?:on|in|using|with) (?:the )?(?:microsoft |google )?(?:edge|chrome|firefox|browser)[:,]?\s*",
    r"^(?:please\s+)?(?:google|bing|duckduckgo)(?:\s+(?:this|that|it|for me))?\s*[:,-]?\s*",
    r"^(?:please\s+)?(?:do an? (?:internet |web |online |quick )?search (?:for|on|about)|"
    r"search(?:ing)? (?:the )?(?:internet|web|online|net)(?: for| about| on)?|search (?:online |the web |the internet )?for|"
    r"look (?:this|that|it) up(?: online)?(?: for me)?[:,]?|look up|find (?:me |out )?|check online for|(?:check|look|search) (?:around |online )?for|"
    r"research|browse the (?:web|internet) for|get me|show me|pull up|dig up|recommend|suggest)\s*[:,-]?\s*",
    r"^(?:on (?:the )?(?:internet|web|youtube)[:,]?\s*)",
]
_TAIL = re.compile(r"[\s,]*(?:online|on the (?:internet|web)|from the (?:internet|web)|on youtube|for me|please|right now|"
                   r"and tell me(?: what you find)?|and let me know)\s*[.!?]*$", re.I)
_YT_WORDS = re.compile(r"\b(?:youtube|video tutorials?|tutorial videos?|videos?)\b", re.I)


def _stripped(text) -> str:
    body = _LEAD.sub("", _bare(text))
    for pattern in _STRIP:
        body = re.sub(pattern, "", body, flags=re.I).strip()
    return body


_QUESTION_START = re.compile(r"^\W*(?:(?:and|also|then|plus)\W+)*(?:who|what|when|where|which|how|why|is|are|do|does|did|will|was|were|can)\b", re.I)


def search_topics(text) -> list[str]:
    """Every topic a search request asks for, in order: the ask itself (split at "and who/what/...") and any later sentence
    that is itself a question ("Search for X. Also what is the weather in Springfield?"). A later statement is not a topic."""
    first = query_of(text, "search")
    topics = webanswer.split_questions(first)
    sentences = webanswer.split_sentences(_stripped(text))
    for sentence in sentences[1:]:
        if _QUESTION_START.match(sentence):
            topics.extend(webanswer.split_questions(sentence.strip(" .!?")))
    return topics


def search_plan(text, limit: int = 2) -> tuple[list[str], list[str], list[str]]:
    """([searched], [not searched: over the limit], [not searched: about him or his household]). A line that asks one thing is
    searched as he asked it; when it asks several, a part about him or his house never goes to a search engine."""
    topics = search_topics(text)
    wide = len(topics) > 1
    kept, private = [], []
    for topic in topics:
        ok, dropped = _scrub(topic, wide)
        kept.extend([ok] if ok else [])
        private.extend(dropped)
    return kept[:limit], kept[limit:], private


_FAM = (r"(?:wife|husband|spouse|partner|girlfriend|boyfriend|fianc\w+|kids?|children|son|daughter|baby|mom|mum|mother|dad|father|grandma|grandpa|grandmother|"
            r"grandfather|grandparents?|sister|brother|aunt|uncle|cousins?|niece|nephew|in-laws?|family|parents)")
# Private, conservatively: my / our / "where I ...", and any "the/his/her/their + a family word" -- EXCEPT where it is plainly the public thing:
# "of ..." ("the wife of Jeff Bezos", "the father of the internet"), "in <Title>" ("the mother in Frozen") and fixed phrases ("mom jeans", "baby shark").
_PUBLIC_FAMILY = r"(?!\s+(?:of\b|in\s+(?-i:[A-Z])|jeans\b|bod\b|shark\b|menu\b|tongue\b|board\b|act\b|are\s+alright\b|guy\b|day\b|dollar\b|feud\b|friendly\b|tree\b|guide\b))"
_MINE = re.compile(r"\b(?:my|our|mine|ours|myself)\b|\bwhere i live\b|\b(?:where|that|which|who|so|because) (?:i|we)\b|"
                   rf"\b(?:the|his|her|their)\s+{_FAM}(?:'s|s'|')?(?!\w){_PUBLIC_FAMILY}", re.I)
_CLAUSES = re.compile(r"\s+(?:and|plus|also|or|then|as well as|along with|where|who|whose|that|which|because|so that)\s+|\s*(?:[,;&/]|\s-\s)\s*", re.I)
_FIRST_PERSON = re.compile(r"^(?:i|we|us|me|i'm|i'd|i'll|we're|we'd|we'll)\b", re.I)
_WHEN_WORDS = re.compile(r"\b(?:tonight|today|tomorrow|this (?:morning|afternoon|evening|weekend|week)|next (?:week|weekend)|on (?:monday|tuesday|wednesday|"
                         r"thursday|friday|saturday|sunday))\b", re.I)


def _private_clause(clause: str, wide: bool) -> bool:
    """Is this clause about him, his household or his house? The names of the household and 'my/our ...' always are; when a line
    asks several things the wider check applies too (the gate code, a family word). 'near me' is not private."""
    if household_named(clause) or _MINE.search(clause):
        return True
    return wide and private_line(re.sub(r"\bme\b", "", clause), strict=False)


def _scrub(topic: str, wide: bool) -> tuple[str, list[str]]:
    """(what may be searched, the clauses left out): every clause of the topic is checked, so 'the weather in Springfield and Sarah's
    school schedule' goes out as the weather only. A topic with nothing public left comes back empty."""
    if not _private_clause(topic, wide):
        return topic, []
    kept, dropped = [], []
    for clause in _CLAUSES.split(topic):
        clause = clause.strip(" .!?")
        if clause:
            # a first-person scrap ("I can go tonight") is what is left of a private clause: never a search of its own
            bare_family = re.search(rf"\b{_FAM}\b{_PUBLIC_FAMILY}", clause, re.I)         # (what is left of "the niece and nephew school")
            (dropped if _private_clause(clause, wide) or _FIRST_PERSON.match(clause) or bare_family else kept).append(clause)
    text = " and ".join(kept)
    if dropped:                                               # "me" never stays in what is left ("flights for me and the wife" -> "flights")
        text = re.sub(r"\s+", " ", re.sub(r"(?i)\b(?:(?:for|with|to|near)\s+)?(?:me|myself|us)\b", " ", text)).strip()
    when = [w for w in _WHEN_WORDS.findall(" ".join(dropped)) if w.lower() not in text.lower()]
    return (text + " " + " ".join(when)).strip() if text else "", dropped


def left_out_say(what, text) -> str:
    """The sentence that tells him which clause of a search was left out for being about him or his household ('' when none was)."""
    if what == "youtube" and not topic_missing(text, what):
        dropped = _youtube_plan(text)[1]
    elif what == "search" and not topic_missing(text, what):
        dropped = search_plan(text)[2]
    else:
        return ""
    dropped = [d for d in dropped if d]
    if not dropped:
        return ""
    return f"I left out {'; '.join(dropped)}, because it is about you or your household and never goes to a search engine."


def over_limit(what, text, limit: int = 2) -> bool:
    """Were public questions left out of the search for lack of room (not: dropped because they were private)?"""
    return what == "search" and not topic_missing(text, what) and bool(search_plan(text, limit)[1])


def _youtube_plan(text) -> tuple[str, list[str]]:
    """(the topic that may be searched on YouTube, the private clauses left out): a YouTube search leaves the machine too."""
    return _scrub(query_of(text, "youtube"), False)


def skipped_note(what, text, limit: int = 2) -> str:
    """What the answer must say about the topics of a search request that were not searched ('' when all were)."""
    if what == "youtube" and not topic_missing(text, what):
        return _not_searched([], _youtube_plan(text)[1])
    if what != "search" or topic_missing(text, what):
        return ""
    _, over, private = search_plan(text, limit)
    return _not_searched(over, private)


def nothing_to_search(what, text) -> str:
    """Said when a search request leaves nothing to search: nothing named, or only things about him and his household."""
    if what == "search" and not topic_missing(text, what) and search_plan(text)[2]:
        return "I won't send that to a search engine: it is about you or your household."
    if what == "youtube" and not topic_missing(text, what) and _youtube_plan(text)[1]:
        return "I won't send that to a search engine: it is about you or your household."
    if what == "local":
        from core import place
        return place.where_am_i()
    return "What would you like me to look up?"


def query_of(text, what="search") -> str:
    """The search topic with the asking stripped: 'Google this: ModuleNotFoundError yaml' -> 'ModuleNotFoundError yaml';
    'Look up three good Blender rigging tutorials on YouTube' -> 'good Blender rigging tutorials'."""
    body = _stripped(text)
    sentences = webanswer.split_sentences(body)
    first = sentences[0] if sentences else body
    if len(first.split()) >= 2 and len(sentences) > 1:
        body = first                                        # "Look up Cocomelon on YouTube. I have it open." -> the ask only
    body = re.sub(r"\s+(?:and )?then\s+(?:open|tell|read|play|show|click|go|give|let|send|email|text|call|save)\b.*$", "", body, flags=re.I)   # his next step
    for _ in range(2):
        body = _TAIL.sub("", body).strip()
    if what == "youtube":
        body = re.sub(r"\b(?:on|in|from|at|using)\s+youtube\b", " ", body, flags=re.I)
        body = re.sub(r"\s+", " ", _YT_WORDS.sub(" ", body)).strip()
        body = re.sub(r"\s+(?:and|then)\s+(?:open|play|click|watch|go to|show)\b.*$", "", body, flags=re.I)
        body = re.sub(r"^(?:(?:me|some|a few|several|a couple of|on|about|for|of|in|from)\s+)+(?=\S)", "", body, flags=re.I) \
            if not re.match(r"(?i)(?:open|launch|go|pull|look|search|find)\b", body) else body
        for _ in range(2):
            body = re.sub(r"^(?:open|launch|go (?:on|to)|pull up)\b[ ,]*(?:and |then )*", "", body.strip(" ,"), flags=re.I)
            body = re.sub(r"^(?:look(?:ing)?|search(?:ing)?|find)(?: for| on| up)?\b[ ,]*", "", body.strip(" ,"), flags=re.I)
        body = re.sub(r"^(?:me\s+)?(?:a few|some|several|a couple of|one|two|three|four|five|\d+)\s+(?:good |great |decent |short )?", "", body, flags=re.I)
    body = re.sub(rf"^(?:the|a|an|some)\s+(?!{_FAM}\b)", "", body.strip(" :,-\"'"), flags=re.I).strip(" .!?:,-\"'")   # ("the wife" keeps its "the": it is the privacy cue)
    body = re.sub(r"\s+", " ", body)
    return body if len(body) >= 3 else _bare(text).strip(" .!?")


_NOTHING_YET = set("um uh er the a an web internet online net google bing search searching for go on and look looking up it this that "
                   "something anything to find open please then youtube".split())


def topic_missing(text, what="search") -> bool:
    """He asked for a search and said nothing to search for ("go on the web and search for, um,"): ask him, search nothing."""
    words = re.findall(r"[a-z0-9']+", query_of(text, what).lower())
    return not [w for w in words if w not in _NOTHING_YET]


def count_of(text, default=3) -> int:
    t = _bare(text).lower()
    m = re.search(r"\b(\d+)\b", t)
    if m:
        return max(2, min(int(m.group(1)), 5))
    for word, n in _COUNT.items():
        if re.search(rf"\b{word}\b", t):
            return max(2, min(n, 5))
    return default


# A request for facts that live inside pages (a version and its release date, an error's fix, a comparison) reads the
# best results too; a plain "google X" lists the results.
_DEEP = re.compile(r"\b(?:latest|newest|most recent|current|version|release|released|date|official|fix|fixes|error|"
                   r"how (?:do|to|can)|why|compare|comparison|vs\.?|versus|difference|explain|details?|specs?|"
                   r"specifications?|price|cost|requirements?|compatible|compatibility)\b", re.I)


_OBJECTIVE = "the answer to the question, with the source and its date"


def _search_call(query: str) -> tuple[str, dict]:
    """The skill for one search topic: facts that live inside pages read the best results; the query that goes out has the
    filler taken off and today's date (or the year) added when the question is about now (core/webanswer.rewrite)."""
    deep = bool(_DEEP.search(query))
    sent = webanswer.rewrite(query) or query
    if deep:
        return "research_online", {"question": sent, "pages": 3}
    return "search_internet", {"query": sent}


def calls_for(what, text, previous: str = "", limit: int = 2) -> list[tuple[str, dict]]:
    """Every skill call a clear request maps to: one, or two when one spoken line asks two things ("the weather in Springfield
    and who won the Lakers game last night"): each is searched, none is silently dropped."""
    if what in ("search", "youtube") and topic_missing(text, what):
        return []                                           # nothing to look up: the caller asks what he wants
    if what == "youtube":
        topic, _ = _youtube_plan(text)
        return [("find_youtube_tutorials", {"topic": topic, "count": count_of(text)})] if topic else []
    if what == "local":
        from core import localask, place
        q = localask.query(text, place.label())
        return [("search_internet", {"query": q, "objective": "what is happening there right now, with each source and its date"})] if q else []
    if what != "search":
        return [call_for(what, text, previous)]
    if not search_topics(text):
        return [call_for(what, text, previous)]
    return [_search_call(p) for p in search_plan(text, limit)[0]]


def call_for(what, text, previous: str = "") -> tuple[str, dict]:
    """The skill and arguments a clear request maps to. `previous`: what he said just before, for "what's the news about
    that?"."""
    if what == "news":
        query = news_query(text, previous)
        sent = webanswer.rewrite(query) or query
        if not _NEWS_ASK.search(_bare(text)) and query != "top news headlines today":
            return "research_online", {"question": sent, "pages": 3}       # "do some research on X": the pages are read
        return "search_internet", {"query": sent,
                                   "objective": "what current news reports say about it, with each source and its date"}
    if what == "youtube":
        return "find_youtube_tutorials", {"topic": query_of(text, "youtube"), "count": count_of(text)}
    return _search_call(query_of(text, "search"))
