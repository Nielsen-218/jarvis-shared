"""Turn what Jarvis writes into what he should SAY (English).

The brain writes for the screen; the voice needs spoken forms. Measured with scripts/voice_clarity.py (Kokoro
speaks, Whisper listens): Kokoro read "Dec. 20" as "deck 20", "62°F" as "62 degrees F", "5V" as "five V", an ISO
date as a subtraction and a URL letter by letter. spoken() fixes those before synthesis. It is deliberately
conservative: what Kokoro already says well (money, clock times, percentages, plain numbers) is left alone, and
only English goes through it (French and Kreyol keep their own voices' rules).
"""
from __future__ import annotations

import os
import re
from datetime import date

_MONTHS = {"jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June",
           "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October",
           "nov": "November", "dec": "December"}
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December")
_DAYS = {"mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
         "thur": "Thursday", "thurs": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_ALT = "|".join(sorted((d.capitalize() for d in _DAYS), key=len, reverse=True))

# "Dec. 20", "Oct 28th", "Jun 2027" -> full month, only before a day or a year ("Mar" in prose stays).
_MONTH_DATE = re.compile(rf"\b({_MONTH_ALT})\.?(?=\s+(?:\d{{1,2}}(?:st|nd|rd|th)?|\d{{4}})\b)", re.I)
# "Mon-Fri" -> "Monday to Friday"; "Thu Oct 23" / "Sat., Dec 20" / "Tue 5" -> full weekday. Capitalised and before a
# date only, so "the cat sat." and "out in the sun" are never touched.
_DAY_RANGE = re.compile(rf"\b({_DAY_ALT})\.?\s*[-–]\s*({_DAY_ALT})\b\.?")
_DAY_DATE = re.compile(rf"\b({_DAY_ALT})\b\.?(?=,?\s+(?:\d|(?i:{_MONTH_ALT})[a-z]*\b))")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:T(?=\d)|\b)")
# "at 17:00" -> "at 5 PM". Only after a time word: "a 13:20 long recording" is a duration.
_CLOCK_24 = re.compile(r"\b(at|by|until|till|from|to|after|before|around)\s+(1[3-9]|2[0-3]|00?):([0-5]\d)(?![\d:])"
                       r"(?!\s*[AaPp]\.?[Mm]\b)", re.I)

# (unit after a number, singular, plural). The number stays as digits: Kokoro reads digits well. Single letters
# (5V, 100W) only touch a digit directly: "120 W 42nd St" is an address.
_UNITS = [(re.compile(rf"(?<![\w.])(\d+(?:[.,]\d+)*){unit}"), one, many) for unit, one, many in [
    (r"\s*°\s*F\b", "degree", "degrees"), (r"\s*°\s*C\b", "degree Celsius", "degrees Celsius"),
    (r"\s*°", "degree", "degrees"),
    (r"\s?mph\b", "mile per hour", "miles per hour"), (r"\s?(?:km/h|kph)\b", "kilometer per hour", "kilometers per hour"),
    (r"\s?k[Ww]h\b", "kilowatt hour", "kilowatt hours"), (r"\s?kW\b", "kilowatt", "kilowatts"),
    (r"\s?mAh\b", "milliamp hour", "milliamp hours"), (r"\s?mA\b", "milliamp", "milliamps"),
    (r"\s?mV\b", "millivolt", "millivolts"), (r"V\b", "volt", "volts"), (r"W\b", "watt", "watts"),
    (r"\s?TB\b", "terabyte", "terabytes"), (r"\s?GB\b", "gigabyte", "gigabytes"), (r"\s?MB\b", "megabyte", "megabytes"),
    (r"\s?KB\b", "kilobyte", "kilobytes"), (r"\s?Mbps\b", "megabit per second", "megabits per second"),
    (r"\s?km\b", "kilometer", "kilometers"), (r"\s?kg\b", "kilogram", "kilograms"), (r"\s?mi\b", "mile", "miles"),
    (r"\s?hrs?\b", "hour", "hours"), (r"\s?mins?\b", "minute", "minutes"), (r"\s?secs?\b", "second", "seconds"),
    (r"\s?ft\b", "foot", "feet"), (r"\s?oz\b", "ounce", "ounces"), (r"\s?lbs?\b", "pound", "pounds"),
    (r"\s?ml\b", "milliliter", "milliliters"), (r"\s?cm\b", "centimeter", "centimeters"),
    (r"\s?mm\b", "millimeter", "millimeters"), (r"\s?GHz\b", "gigahertz", "gigahertz"),
    (r"\s?MHz\b", "megahertz", "megahertz"), (r"\s?kHz\b", "kilohertz", "kilohertz"), (r"\s?Hz\b", "hertz", "hertz"),
    (r"\s?fps\b", "frame per second", "frames per second"),
]]

_ABBR = [(re.compile(p, flags), word) for p, word, flags in [
    (r"\be\.g\.,?", "for example", re.I), (r"\bi\.e\.,?", "that is", re.I), (r"\betc\.", "et cetera", re.I),
    (r"\bvs\.?(?=\s)", "versus", re.I), (r"\bapprox\.", "approximately", re.I),
    (r"\bDr\.(?=\s+[A-Z])", "Doctor", 0), (r"\bMr\.(?=\s+[A-Z])", "Mister", 0), (r"\bMrs\.(?=\s+[A-Z])", "Missus", 0),
    (r"\bSgt\.(?=\s+[A-Z])", "Sergeant", 0), (r"\bLt\.(?=\s+[A-Z])", "Lieutenant", 0),
    (r"\bCol\.(?=\s+[A-Z])", "Colonel", 0), (r"\bCapt\.(?=\s+[A-Z])", "Captain", 0),
    (r"\bAve\.", "Avenue", 0), (r"\bBlvd\.", "Boulevard", 0), (r"\bRd\.", "Road", 0),
    (r"\b[Nn]o\.(?=\s*\d)", "number", 0), (r"\bw/o\b", "without", re.I), (r"\bw/(?=\s)", "with", re.I),
]]

_URL = re.compile(r"(\()?\b(?:https?://|www\.)\S+?(?(1)\))(?=[,;:!?.)]*(?:\s|$))")
_EMOJI = re.compile("[%s-%s%s-%s%s-%s%s%s]" % (chr(0x1F300), chr(0x1FAFF), chr(0x2600), chr(0x27BF),
                                              chr(0x1F1E6), chr(0x1F1FF), chr(0x200D), chr(0xFE0F)))


def _iso(m: re.Match) -> str:
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    when = f"{_MONTH_NAMES[mo - 1]} {d}" + ("" if y == date.today().year else f", {y}")
    return when + (" at " if m.group(0).endswith("T") else "")


def _clock(m: re.Match) -> str:
    hour, minute = int(m.group(2)), m.group(3)
    return f"{m.group(1)} {hour % 12 or 12}{'' if minute == '00' else ':' + minute} {'AM' if hour < 12 else 'PM'}"


def spoken(text: str) -> str:
    """Display text -> speakable English. Idempotent; clean text comes back unchanged."""
    if not text or not text.strip():
        return text
    s = text
    # screen formatting is not speech
    s = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", s)
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", s)
    s = re.sub(r"(?m)^\s*(?:[-*•]|\d{1,2}[.)])\s+", "", s)          # list markers at line start
    s = re.sub(r"(?<=[.!?:;])\s+[-•]\s+", " ", s)                    # "...board. - Step 2" inline bullets
    s = re.sub(r"\[([^\]]+)\]\((?:https?://)?[^)]+\)", r"\1", s)     # [label](link) -> label
    s = _URL.sub("the link", s)
    s = _EMOJI.sub("", s)
    # dates and times
    s = _ISO_DATE.sub(_iso, s)
    s = _CLOCK_24.sub(_clock, s)
    s = _MONTH_DATE.sub(lambda m: _MONTHS[m.group(1).lower()], s)
    s = _DAY_RANGE.sub(lambda m: f"{_DAYS[m.group(1).lower()]} to {_DAYS[m.group(2).lower()]}", s)
    s = _DAY_DATE.sub(lambda m: _DAYS[m.group(1).lower()], s)
    # abbreviations and units
    for rx, word in _ABBR:
        s = rx.sub(word, s)
    for rx, one, many in _UNITS:
        s = rx.sub(lambda m, one=one, many=many: f"{m.group(1)} {one if m.group(1) == '1' else many}", s)
    # symbols
    s = re.sub(r"\s&\s", " and ", s)
    s = re.sub(r"(?<![\w~])~\s*(?=\d)", "about ", s)
    s = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", " by ", s)                  # 1920x1080, 2x4
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s


# ------------------------------------------------------------------------------------------- how JARVIS sounds (upgrade 4)
# What the model writes, said the way JARVIS says it, sentence by sentence as it streams (Brain) or for a whole reply
# (polish). Measured in the owner's logs: qwen3.5 closes nearly every reply with an offer ("Anything else?"); a stale reply
# talked about "the owner" in the third person to his face (Nov 9); "Why are you here?" got the same four-sentence spiel
# twice in a row (Nov 9 18:2x). And in scripts/latest_answer_eval.py (real model, 2026-11-10): 23 of 26 answers ended on
# an offer to keep or show what was just said ("Shall I save that fact?", "Shall I have you open a map to show it?"), and
# an answer to an EARLIER question came back for a new one ("What's a synonym for happy?" -> "Gracias, sir."; "How many
# ounces are in a pound?" -> "The square root of 144 is 12, sir."). Nothing here adds a claim: it only drops or rewords,
# and never a fact: a new reading (a tool ran this turn), a changed number or a 'not' is never treated as a copy.
REPEAT_SIMILARITY = 0.85
SAME_QUESTION = 0.6          # his question reads at least this alike to an earlier one: the same answer is right
_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DECLINES = re.compile(r"(?:no|nope|nah|not now|never ?mind|cancel|stop|forget it|leave it|don'?t)\b", re.I)
# Only 'the owner' who told JARVIS to do something is him ("The owner had previously asked me to work on it", Nov 9). Any
# other owner (of a repo, a shop, a folder, a listing) is someone else and stays as written: rewriting it made false
# statements about him ("You archived that repo", "You are listed as SYSTEM").
_OWNER_TO_ME = re.compile(r"\b(the owner)(\s+(?:has|had)\b)?((?:\s+(?:previously|already|just|earlier|also|first|originally|"
                          r"specifically|once))?)\s+(asked|told|wanted|wants|needed|needs|instructed|requested|reminded)(\s+me)\b",
                          re.I)
_SECOND_PERSON_VERB = {"has": "have", "wants": "want", "needs": "need"}
# He asked to hear it again (or did not hear it): a repeat is the answer, not a copy to cut.
_ASKED_AGAIN = re.compile(r"\b(?:again|repeat|come again|pardon|say that|one more time|what did you (?:just )?say|"
                          r"what was that|missed (?:that|it|what you said)|"
                          r"(?:did ?n[o']?t|could ?n[o']?t|can'?t|cannot) (?:quite )?(?:hear|catch|get|make out) "
                          r"(?:you|that|it|what you said))\b|"
                          r"^\W*(?:(?:what|sorry|huh|eh)\W*)+$", re.I)
# He asks about something said earlier ("What was the capital you mentioned?", "What did we decide at the start?"): an
# earlier answer is the answer.
_REFERS_BACK = re.compile(r"\b(?:earlier|before|previous(?:ly)?|last time|at the start|mention\w*|remind me|what was|"
                          r"which (?:one|was)|you (?:said|told|gave|suggested|recommended|named|picked|meant)|"
                          r"did you (?:just )?(?:say|tell|give|suggest|recommend|name|pick|mean|call)|do you mean|"
                          r"we (?:said|decided|agreed|talked|discussed|picked|chose|called))\b|"
                          # "Wait, which city?", "Which city?": checking a detail of what was just said
                          r"^\W*(?:wait|hold on|hang on)\b|^\W*(?:(?:and|so)\W+)?which \w+\W*$", re.I)
# He follows on from the last exchange ("And who runs SpaceX?", "So what about tomorrow?"): its answer may well be his.
_FOLLOW_ON = re.compile(r"^\W*(?:and|so|then|also|what about|how about)\b", re.I)
# A closing offer that proposes nothing ("Anything else?", "Would you like me to look into it further?"). A question that
# names a step ("Want me to restart it?", "Should I close Edge?") is a real question the persona asks first: never cut.
_CLOSING_OFFER = re.compile(r"(?:\banything (?:else|more|further|i can)\b|\bneed anything\b|\bhow (?:else )?(?:can|may) i (?:help|assist)\b|"
                            r"\bwhat else (?:can|could|may|shall) i\b|\bwhat (?:can|could|may) i (?:do|help)\b|"
                            r"\b(?:would you like|do you want|want|shall i|should i)\b[^?]*?\b(?:further|more (?:details?|information|"
                            r"info)|to (?:know|hear) more|(?:tell|show) you more|(?:go|dig) deeper|elaborate)\b)[^?]*\?\s*$", re.I)
# ...and an offer of something he did not ask for: to keep what was just said ("Shall I save that fact?", "Shall I log that
# under Falcon?", "Shall I mark that date on your calendar?") when he asked to keep nothing (intelligence._SAVE_CUES), or
# to open something to show it ("Shall I have you open a map to show it?") when he asked to see nothing. A step he must say
# yes to first (restart it, close Edge) or a lesson page (the persona offers those) is never one of these.
_OFFER_OPENER = (r"^\W*(?:(?:so|and|also|now|well|then|or|sir|okay|ok|maybe|perhaps|meanwhile)[,\s]+)*"
                 r"(?:(?:shall|should|can|could|may) (?:i|we)|(?:do you )?want me to|would you like(?: me)? to|"
                 r"do you want(?: me)? to|how about i)\b")
_KEEP_IT = re.compile(_OFFER_OPENER + r"[^?]*?\b(?:save|log|note|jot|record|remember|store|keep|file|bookmark|"
                      r"write (?:it|that|this|them) down|add (?:it|that|this|them|those)\b|mark (?:it|that|this|the)\b|"
                      r"set (?:a|up a|you a) reminder|remind you|put (?:it|that|this) (?:in|on|under))\b[^?]*\?\s*$", re.I)
_SHOW_IT = re.compile(_OFFER_OPENER + r"[^?]*?\b(?:open|pull up|bring up|show|display|find|look up|get)\b[^?]*?"
                      r"\b(?:maps?|diagrams?|dictionar(?:y|ies)|thesaurus|pictures?|photos?|images?|charts?|calculator|"
                      r"thermometer|scales?|colou?r wheel|books?|calendar|videos?|articles?|details|definitions?|websites?|"
                      r"wiki\w*|recipes?|graphics?|illustrations?|globe|encyclopedia)\b[^?]*\?\s*$", re.I)
_SEE_CUES = re.compile(r"\b(?:show|open|see|look|display|pull up|bring up|map|picture|photo|image|diagram|video|recipe|"
                       r"calendar|website|page|link)\b", re.I)
# ...or one that drags in something else ("Would you like a recipe to use up something in the pantry?" after "What's the
# largest ocean?"; "Shall I check the weather for your commute tomorrow?" after the freezing point of water, eval
# 2026-11-10): not one word of it is about what he asked or what was just answered, and it refers back to nothing.
_ANY_OFFER = re.compile(r"^\W*(?:(?:so|and|also|now|well|then|or|sir|okay|ok|maybe|perhaps|meanwhile)[,\s]+)*"
                        r"(?:(?:shall|should|can|could|may) (?:i|we)|(?:do you )?want(?: me to| to| a| an| some| the)|"
                        r"would you like(?: me)?(?: to| a| an| some| the| any| more)|"
                        r"do you want(?: me)?(?: to| a| an| some| the| any| more)|"
                        r"how about (?:i|we)|fancy (?:a|an|some))\b[^?]*\?\s*$", re.I)
# Review 2026-11-11: also the steps on his PC, apps and messages ("Shall I lower the volume?", "Shall I launch the
# installer?", "Shall I text Ana a heads-up?"), and the -ing / -ed forms ("Shall I try uninstalling it again?"). Never a
# bare save / open / play / show: "Shall I save that fact?" and "Shall I open a map to show it?" are the unasked offers.
_ASK_FIRST = re.compile(r"\b(?:restart(?:s|ed|ing)?|repair(?:s|ed|ing)?|fix(?:es|ed|ing)?|delet(?:e|es|ed|ing)|"
                        r"remov(?:e|es|ed|ing)|uninstall(?:s|ed|ing)?|install(?:s|ed|ing)?|clos(?:e|es|ed|ing)|"
                        r"quit(?:s|ting)?|kill(?:s|ed|ing)?|stop(?:s|ped|ping)?|end|reboot(?:s|ed|ing)?|reset(?:s|ting)?|"
                        r"rescan|retry|try (?:it |that )?again|go ahead|proceed|throttle|cancel|update|upgrade|move|clean|"
                        r"free up|turn (?:it |that |them )?(?:off|on)|switch|enable|disable|"
                        r"launch|lock(?! (?:(?:it|that|this|them) )?in\b)|unlock|lower|raise|mute|unmute|pause|resume|"
                        r"download|send(?! (?:it|that|this|them) to (?:your |the |a )?(?:notes?|log|journal|list))|text|"
                        r"e-?mail|message|order|print|reply|rebuild|"
                        r"(?:run|start) (?:it|that|them|the)\b(?! (?:(?!and\b)\w+ ){0,2}(?:lesson|log|notes?|journal|list)\b)|"
                        r"shut (?:it )?down|sign out|log out|empty|eject|turn (?:it |that |them |the \w+ )?(?:up|down)|"
                        r"save (?:the |your |all )?(?:changes|file|document|work|draft|scene|project)"
                        r"(?! (?:name|idea|notes?|log))|save (?:it|them) before)\b", re.I)
# ...but a clause that puts something in his notes, a log, a journal or a list keeps it, whatever its verb ("Shall I send
# that fact to your notes?", "Shall I save the text of that line to your notes?", review 2026-11-11): no step (_a_step).
_KEEP_TARGET = re.compile(r"\b(?:to|in|into|on|under) (?:your |the |a |my )?(?:\w+ ){0,2}(?:notes?|log|journal|list)\b",
                          re.I)
_REFERS = re.compile(r"\b(?:it|its|that|this|them|those|these|they|one|ones)\b", re.I)
# The persona's own offers (core/personality.md), never an unasked one: text put on the clipboard, a video's transcript,
# his real measurements for exact sizes, a finished download moved into movies or anime, the conversation language
# locked, outfits previewed side by side.
_PERSONA_OFFER = re.compile(r"\b(?:clipboard|transcripts?|measurements|folders?|(?:in(?:to)?|under|to) (?:the |your )?"
                            r"(?:movies|anime)|language|side by side)\b", re.I)
# He asked for something to be DONE, worded as a question ("Can you remind me to call Marcus at 5?", "Could you add eggs
# to my shopping list?"): the question back confirms or completes his own request, it is no offer he did not ask for.
# Never a question for information worded as a request ("Can you convert five miles to kilometers?", "Could you translate
# thank you into French?", "Would you happen to know...?", review 2026-11-11): an offer after one drags in as after any
# question. ('Can you find me ...' stays a request: what was found is often his to book, send or keep.)
_DO_IT = re.compile(r"^\W*(?:(?:hey|ok|okay|so|um|uh|and|please)\W+)*(?:jarvis\W+)?(?:(?:can|could|would|will) you|please)"
                    r"\s+(?:please\s+)?(?!tell\b|explain\b|describe\b|say\b|recommend\b|suggest\b|name\b|give me\b|"
                    r"think of\b|help me (?:understand|decide|choose|pick)\b|find out\b|look up\b|check\b|search\b|"
                    r"figure out\b|see\b|guess\b|believe\b|translate\b|define\b|convert\b|spell\b|calculate\b|"
                    r"work out\b|happen to\b|remind me (?:what|who|when|where|how)\b)\w", re.I)
# He asks for advice or a suggestion ("What should I make for dinner?", "Can you recommend a laptop?", "What's a good
# workout for today?"): a question back about what he would like is part of the answer, and so is tying it to what he
# said earlier ("No mushrooms in it, and it's vegetarian."). Also the everyday ways he asks for a pick ("What's for
# dinner?", "I'm hungry.", "Any thoughts on lunch?", review 2026-11-11): "Would you like a chicken stir-fry?" answers them.
# (Known and left as it is: 'good' also matches "Good morning", which keeps an offer or an earlier topic after it, as main
# does.)
_ADVICE = re.compile(r"\b(?:recommend\w*|suggest\w*|ideas?|advice|advise|tips?|should (?:i|we)|what (?:can|could) "
                     r"(?:i|we)|help me (?:choose|pick|decide|find)|good|best|"
                     r"what(?:['’]s| is) for (?:dinner|lunch|breakfast|supper)|hungry|starving|thoughts on|"
                     r"what (?:to|do i|do we|shall i|shall we) (?:eat|have|make|cook|watch|play|do))\b", re.I)
# A question about what he would like ("Would you like something quick?", "Should we go with chicken?"), as opposed to
# an offer to do something ("Shall I check the weather?", "Would you like me to...").
_PREFERENCE = re.compile(r"^\W*(?:(?:so|and|also|now|well|then|or|sir|okay|ok|maybe|perhaps)[,\s]+)*"
                         r"(?:would you (?:like|prefer)(?! me\b)|do you (?:want|prefer|like)(?! me\b)|should we|shall we|"
                         r"fancy|are you (?:in the mood|after|looking|thinking))\b", re.I)
# A sentence that ties the answer back to it ("No mushrooms in IT", "BOTH go easy on a hurting knee").
_TIES_BACK = re.compile(r"\b(?:it|its|they|them|those|these|both|either|each)\b", re.I)
# The persona offers a lesson page when they ask to be taught or have something explained; after a quick fact ("Shall I
# open a lesson page on arachnids?" for a spider's legs, eval 2026-11-10) it is one more unasked offer.
_LESSON_PAGE = re.compile(r"\blesson page\b", re.I)
_EXPLAIN = re.compile(r"\b(?:explain|teach|lesson|learn|how (?:does|do|did|is|are|can|would)|why (?:does|do|did|is|are)|"
                      r"walk me through|what(?:'s| is) the difference|in detail|go deeper)\b", re.I)
# Words that may come before a closing offer and still leave it a pure offer ("Sir, is there anything else?").
_OFFER_LEAD = {"is", "there", "sir", "ma'am", "so", "and", "also", "now", "well", "then", "or", "oh", "okay", "ok", "just",
               "maybe", "perhaps", "else"}
_NEGATIONS = {"not", "no", "never", "none", "nothing", "nobody", "nowhere", "neither", "nor", "without", "cannot"}
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
                 "million", "billion", "half", "quarter", "percent", "degrees"}
# Words that say nothing about WHAT an answer is (titles, fillers, contractions intelligence._STOP keeps).
_EMPTY = {"maam", "madam", "mesye", "monsieur", "madame", "thats", "theres", "heres", "youre", "theyre", "dont", "doesnt",
          "isnt", "arent", "wasnt", "whos", "hows", "wheres", "whens", "certainly", "indeed", "well", "right", "okay",
          "sure", "yes", "yeah", "answer", "shall", "lets"}
OLDER_EXCHANGES = 12          # how far back an answer to an earlier question is looked for
# A sentence made only of these says nothing yet ("Certainly, sir.", "Of course, sir.", "Very good, sir.").
_ACKNOWLEDGES = {"cours", "understood", "absolutely", "very", "good", "gladly", "alright", "fine", "great", "excellent",
                 "perfect", "done", "happy", "help", "pleasure", "course", "check", "moment", "sec", "second",
                 "minute", "look", "hold", "hang", "one"}
# ...and these openers, matched on their own words (review 2026-11-11): the stemmer makes 'noted' 'not' and 'indeed'
# 'inde', and "Sure thing" / "Good question" / "No problem" have a word of their own. Never the stem 'not' in the list
# above: "No, sir." and "Not at all, sir." are answers.
_ACK_PHRASE = re.compile(r"^\W*(?:sure thing|(?:good|great|excellent|fair|interesting) question|no problem|noted|indeed|"
                         r"hello|hi|copy that|roger(?: that)?)\W*(?:(?:sir|ma'am|ma’am|madam)\W*)?$", re.I)
# What was said admits a gap ("I don't have their prices yet.", "I'd rather not guess."): an offer to find out is the way
# forward, not a topic of its own (drags_in).
_UNKNOWN = re.compile(r"\b(?:(?:i|we)\s+(?:do not|don't|didn't|did not|can't|cannot|couldn't|could not|haven't|have not)\s+"
                      r"(?:\w+\s+)?(?:know|have|see|find|get|tell|check|compare)|not sure|rather not guess|no idea|"
                      r"(?:i'm|i am) unsure)\b", re.I)
_CAPS = re.compile(r"\b[A-Z]{2}\b")


def _flat_words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", (text or "").replace("’", "'").casefold())


def _caps(text: str) -> set:
    """Its two-letter names in capitals ('PC', 'TV', 'AI', 'IP'), which _content leaves out as too short: a sentence about
    his PC is about what he asked when he asked about his PC (drags_in and _dragged only)."""
    return {w.casefold() for w in _CAPS.findall(text or "")} - {"ok"}


# The last reply asked or offered something ("Want me to set a timer?", "I can look that up for you if you like.").
_OFFERED = re.compile(r"\?|\b(?:i|we) (?:can|could)\b[^.!?]*\b(?:for you|if you(?:'d| would)? like|if you want)\b", re.I)


def _accepts(said: str, offered: str) -> bool:
    """His yes, and nothing else ('Go ahead.', 'Sure.', 'Sounds good.', 'Yes please'), to what the last reply (`offered`)
    asked or offered: a follow-on, never a different question ('ahead' and 'sound' would read as a topic of their own).
    After a reply that offered nothing it says yes to nothing there, and a copy of that reply answers nothing."""
    from core import intelligence
    text = (said or "").replace("’", "'")
    return (bool(intelligence._ACCEPTANCE.search(text)) and not _content(intelligence._ACCEPTANCE.sub(" ", text))
            and bool(_OFFERED.search((offered or "").replace("’", "'"))))


def second_person(sentence: str) -> str:
    """'The owner had asked me' -> 'You had asked me' (the verb agreeing: 'the owner wants me' -> 'you want me'). Only the
    owner who told JARVIS something; every other 'the owner' is left alone."""
    def swap(m: re.Match) -> str:
        aux = m.group(2) or ""
        if aux.strip().lower() == "has":
            aux = aux.replace(aux.strip(), "have")
        verb = _SECOND_PERSON_VERB.get(m.group(4).lower(), m.group(4)) if not aux else m.group(4)
        word = "you" + aux + (m.group(3) or "") + " " + verb + m.group(5)
        return word[0].upper() + word[1:] if m.group(1)[0].isupper() else word
    return _OWNER_TO_ME.sub(swap, sentence)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").casefold()))


def _similar(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _facts(text: str) -> tuple:
    """The parts of a sentence a near-copy may not differ in: its numbers and its negations ('45 percent' vs '92 percent',
    'is still running' vs 'is not running' read 0.93-0.95 alike, and are different facts)."""
    words = _norm(text).split()
    return (sorted(w for w in words if any(c.isdigit() for c in w) or w in _NUMBER_WORDS),
            sorted(w for w in words if w in _NEGATIONS or w.endswith("n't")))


def _content(text: str) -> set:
    """What a sentence is about: its content words (stemmed, as intelligence._words) and its numbers."""
    from core import intelligence
    low = (text or "").casefold().replace("’", "'")
    return (intelligence._words(low, hints=False) | set(re.findall(r"\d+(?:[.,]\d+)*", low))) - _EMPTY


# The chat path (core.chatturn): the reply is only talk, nothing can be opened, looked up or done in it, so a last
# sentence that offers to ("Want me to pull up today's workout or check on the rig?"), asks what to do next ("Anything
# specific you want me to tackle next?") or leaves the door open ("Let me know if you need anything.") is the model's
# habit, not a question his answer needs. Film JARVIS says his line and stops (owner, 2026-11-12).
# Only an offer for JARVIS to do a task counts (review 2026-11-12): "May I ask what happened?", "Would you like to talk
# about it?", "Do you need a break?", "Should I be worried?", "What's next for the project?" are conversation.
_TASK = (r"open|pull\s+up|bring\s+up|check|set|look\s+(?:up|into|at|for|through)|run|start|prepare|get|fetch|find|search|"
         r"play|put|make|draft|write|send|schedule|book|remind|note|save|add|create|draw\s+up|show|read|order|turn|"
         r"switch|close|launch|go\s+(?:through|over|on|ahead)|help|do|handle|sort|take\s+care|tackle|arrange|organi[sz]e|"
         r"plan|line\s+up|queue|call|text|e-?mail|fix|update|install|download|tell\s+you\s+more|walk\s+you\s+through|dim|"
         r"lower|raise|mute|pause|resume|continue|carry\s+on|keep|stay|leave|log|track|list|pick|dig|review|summari[sz]e")
# "May I ask what happened?", "Should I be worried?", "Can I just say...": Jarvis asking about himself or for leave to
# speak, not offering a task. On the chat path it is conversation, whatever topic it has (never drags_in).
_ASKS_LEAVE = re.compile(r"^\W*(?:(?:so|and|also|now|well|then|or|but|sir|okay|ok|maybe|perhaps)[,\s]+)*"
                         r"(?:shall|should|can|could|may|might) i\b(?!\s+(?:(?:just|quickly|also|maybe|perhaps|go\s+ahead\s+and)"
                         r"\s+)*(?:" + _TASK + r")\b)", re.I)
_CHAT_OFFER = re.compile(
    r"^\W*(?:(?:so|and|also|now|well|then|or|but|sir|ma'am|okay|ok|maybe|perhaps|meanwhile|otherwise|in the meantime|"
    r"if so|if not|either way)[,\s]+)*"
    r"(?:(?:shall|should|can|could|may|might) i\s+(?:(?:just|quickly|also|maybe|perhaps|go\s+ahead\s+and)\s+)*(?:" + _TASK + r")\b|"
    r"(?:do you |did you |you )?(?:want|need|wish|like) me to\b|would you (?:like|prefer|want) me to\b|how about i\b|"
    r"(?:is there )?(?:anything|something)\b[^.?!]*\b(?:you(?:'d| would)? (?:want|need|like) (?:me to|from me|done|"
    r"handled|sorted)|you (?:want|need)\b[^.?!]*\bfrom me|i (?:can|could|should|might) (?:" + _TASK + r"))\b|"
    r"what (?:else )?(?:can|could|shall|should|may) i (?:" + _TASK + r")\b|how (?:else )?(?:can|may|could) i (?:help|assist|"
    r"be of)\b|"
    r"(?:just )?let me know (?:if|when|whenever|should|what|once)\b|(?:just )?say the word\b|give me a shout\b|"
    r"feel free to (?:ask|call|say|shout|let me know)\b|"
    r"(?:i'm |i am |i'll be )?(?:just |right |always )?here (?:if|when|whenever|should)\b|"
    # A task prompt at the end of small talk (live 2026-11-12): "What do you need right now?", "What is your priority?",
    # "I will handle whatever you need me to do."
    r"what (?:do|would) you (?:need|want)(?: me to do)?(?: right now| now| today| next)?\s*\??\W*$|"
    r"what(?:'s| is) your (?:priority|plan|first move)(?: right now| now| today)?\s*\??\W*$|"
    r"what (?:needs|should) (?:to be )?(?:done|handled)(?: right now| now| today| next| first)?\s*\?\W*$|"
    r"(?:i(?:'ll| will| shall) |i can |i am here to )handle (?:whatever|anything|everything)(?: you (?:need|want|require)(?: me to do)?| is needed)?\W*$|"
    r"i(?:'m| am) (?:ready|here|standing by|at your service)(?:,? sir)?(?: for (?:whatever|anything)(?: you (?:need|want))?| to (?:assist|help|serve)(?: you)?)\W*$|"
    r"if you(?:'d| would)? (?:like|want|need|prefer|wish)\b[^.?!]*\b(?:i (?:can|could|will|'ll)|let me know|just (?:say|ask)|"
    r"say the word)\b|"
    r"i (?:can|could)\b[^.?!]*\bif you(?:'d| would)? (?:like|want|need|prefer|wish)\b|"
    r"(?:i'd be |i am |i'm )?happy to\b[^.?!]*\bif\b)", re.I)
# He asked what Jarvis can do, or for help or a suggestion: then an offer is the answer.
_ASKED_FOR_OFFER = re.compile(
    r"\bwhat (?:else )?(?:can|could|do) you (?:do|help|offer)\b|\b(?:can|could|will|would) you (?:help|do|handle|sort|take care)\b|"
    r"\bhow (?:can|could) you help\b|\bwhat are (?:my|the|your) options\b|\bwhat do you (?:suggest|recommend|propose)\b|"
    r"\bany (?:ideas|suggestions)\b|\bwhat (?:should|shall|can|could) (?:i|we) do\b|\bhelp me\b|\bi need (?:your )?help\b|"
    r"\bwhat are you (?:able|capable)\b|\bwhat(?:'s| is) next\b|\bwhere (?:do|should|shall) (?:i|we) start\b", re.I)


# The tool-manual path (a reply after a skill ran, or one that answers a request): a trailing sentence that only hands the
# conversation back ("Just say which program you'd like next.", "What else would you like me to know?", "Is there anything
# else?", "I can open, close or search for programs.", "Let me know if you need anything."). Narrow on purpose: a question
# that names what is needed for THIS request ("Which one did you mean?", "Notepad or Paint?", "Shall I close it?") is never one.
_MANUAL_THING = (r"(?:program|app|application|software|tool|task|command|skill|thing|action|feature|function|option|"
                 r"request|job|project|topic|subject)s?")
_MANUAL_CLOSER = re.compile(
    r"^\W*(?:(?:so|and|also|now|well|then|or|but|sir|ma'am|okay|ok|otherwise|in the meantime)[,\s]+)*(?:"
    # Opt-in by phrase. A generic "which program / what you want" that ends on nothing or "next/now" is a hand-back; a request
    # for the missing detail of THIS request (which contact, what time, which song, what program to close) is never one.
    r"(?:just |simply )?(?:say|tell me|let me know|name|give me|type|ask)\b[^.?!]*\b(?:which|what|whichever|whatever)\s+"
    r"(?:" + _MANUAL_THING + r"\s+)?(?:you|i)(?:'d| would)?\s+(?:like|want|need)(?:\s+(?:next|now|then|else|from here))?\W*$|"
    r"(?:just |simply )?(?:say|tell me|let me know|ask)\b[^.?!]*\b(?:whenever|when you|if you|once you)\b[^.?!]*\b(?:need|want|like|"
    r"ready|anything|something|help|else)\b[^.?!]*$|"
    r"(?:just |simply )?(?:say|tell me|let me know|ask)\b[^.?!]*\b(?:anything|something)\s+(?:else|more)\b[^.?!]*$|"
    r"(?:just |simply )?(?:say|tell me|let me know|ask)\b[^.?!]*\b(?:next|else|more)\W*$|"
    r"what else (?:would you like|do you want|do you need|can i|could i|shall i|may i)"
    r"(?:\s+(?:me\s+)?(?:to\s+)?(?:do|know|check|hear|see|help(?: with)?|try|open|run))?"
    r"(?:\s+(?:for you|today|now|next|from me))*\W*$|"
    r"what (?:would|do) you (?:like|want) (?:me )?to (?:do(?: next| now| then| from here)?|(?:try|open|run|check|know|hear|see)"
    r"(?: next| now| then| from here))\W*$|"
    r"what (?:" + _MANUAL_THING + r") (?:would|do|shall|should) you (?:like|want|need)(?:\s+to (?:try|do))?"
    r"(?:\s+(?:next|now|else|then))?\W*$|"
    r"(?:is|are) there (?:anything|something) (?:else|more|further|other|i can|we can)\b[^.?!]*\??\W*$|"
    r"anything (?:else|more|further)\b[^.?!]*\??\W*$|"
    r"(?:i can|i could|i am able to|i'm able to) (?:also )?\w+(?: \w+){0,2}(?:,| or | and )[^.?!]*\b(?:or|and)\b[^.?!]*"
    r"(?:" + _MANUAL_THING + r"|files?|windows?|apps?|things|more|settings?|folders?|documents?|tabs?|reminders?|timers?)\W*$|"
    r"(?:i'm |i am |i'll be )?(?:just |right |always )?(?:here|standing by|at your service|ready)\b[^.?!]*\b(?:if|when|whenever|should|for)\b|"
    r"feel free to\b|give me a shout\b|(?:just )?say the word\b|(?:just )?ask (?:me )?(?:if|when|whenever|and)\b)", re.I)
# Jarvis as a help desk on call (the live log of 2026-11-20, after commands that had found no route): "I'm here and ready to help with
# whatever you need.", "I'm here and ready for your next command.", "Tell me what you need done.", "What do you need me to handle first?",
# "What's the first thing you want me to handle in there?", "I'm here to be your partner, sir." None asks for a detail the request needs.
_ON_CALL = re.compile(
    r"\bi(?:'m| am) (?:here|ready|listening)(?: and (?:ready|listening|here))?\b[^.?!]{0,12}"
    r"(?:to help|to assist|for (?:your next|whatever|anything)|with whatever|whenever)\b[^.?!:,;]{0,30}(?:,\s*\w+)?[.!]?\s*$|"
    r"\bready to (?:listen|help|assist)\b[^.?!:;]{0,25}\b(?:whatever|anything|however)\b|"
    # "ready for your next command", never "The next task on your list is the dentist." (review 2026-11-21)
    r"\b(?:ready for|waiting for|awaiting) (?:your|the) next (?:command|instruction|order|request|task)\b|"
    r"^\W*(?:(?:so|and|now|just|okay|ok|sir)[,\s]+)*tell me what you (?:need|want)(?: done| me to do| next)?\W*$|"
    r"^\W*(?:(?:so|and|now|okay|ok|sir)[,\s]+)*what (?:do|would) you (?:need|want|like) me to (?:\w+ or )?(?:do|handle|run|tackle|build|fix|work on|"
    r"take care of|start (?:with|on)|look at)(?: first| next| now| in there| for you)?(?:,? sir)?\s*\?\W*$|"
    r"\bwhat(?:'s| is) the first thing (?:you(?:'d| would)? (?:want|need|like)|for me)\b|"
    r"\bi(?:'m| am) (?:here )?(?:as |to be )?your (?:partner|assistant|servant)\b", re.I)
_GENUINE = re.compile(r"\b(?:which (?:one|of|window|file|folder|version|account|drive|device|tab|document)|did you mean|do you mean|"
                      r"you meant|the first or|first or second|one of (?:these|those|the)|"
                      r"(?:which|what) (?:time|name|number|contact|song|recipient|date|day|message|title|city|address|subject|body|"
                      r"person|channel|playlist|artist|album|room|temperature|level|amount)|anything specific)\b", re.I)


# The character sheet's acknowledgements (core/character.py): no butler words ("Very good", "Quite", "Indeed": wave 3b 2026-11-21)
from core.character import LINES as _CHARACTER_LINES  # noqa: E402
_ACK_VARIANTS = _CHARACTER_LINES["ack"]


def echoes_ack(sentence: str, asked: str) -> bool:
    """Is this reply only his own acknowledgement said back ("Right." to "Right.", "Okay, sir." to "Okay.")? His line is
    nothing but interjections or an acknowledgement (no question, no content), and the reply is at most four words that
    are all his words, a title or a name for him."""
    from core import chatturn
    said = (asked or "").replace("\u2019", "'")
    if not said.strip() or "?" in said or not chatturn.fragment_talk(said):
        return False
    heard = set(re.findall(r"[a-z']+", said.casefold()))
    mine = re.findall(r"[a-z']+", (sentence or "").replace("\u2019", "'").casefold())
    return bool(mine) and len(mine) <= 4 and set(mine) <= heard | {"sir", "ma'am", "madam", "boss", "then"}


def other_ack(asked: str, title: str = "sir", last: str = "") -> str:
    """A brief acknowledgement that is not his word back and not what was said last (same line, same pick: no dice)."""
    heard = set(re.findall(r"[a-z']+", (asked or "").casefold()))
    if re.search(r"\bthank|\bcheers\b", asked or "", re.I):
        pick = ("Not at all", "My pleasure", "Any time")[sum(map(ord, asked or "")) % 3]
    else:
        options = [v for v in _ACK_VARIANTS if v.casefold() not in heard and v.casefold().split()[0] not in (last or "").casefold()]
        pick = (options or list(_ACK_VARIANTS))[sum(map(ord, asked or "")) % len(options or _ACK_VARIANTS)]
    from core import character
    return character.titled(pick + ".", title) if title and character.title_due() else f"{pick}."


# The filler a reply to an acknowledgement ends on: it hands the turn back and asks for nothing the work needs. Anything
# not in this closed set is a real question (a confirmation, a choice, a detail) and is always kept.
_ACK_FILLER = re.compile(
    r"^\W*(?:(?:and|so|now|well)[,\s]+)?(?:"
    r"what (?:do you want|would you like|shall we|should we|else)\b[^?]*\b(?:next|now|learn|explore|cover|else|from here)\b|"
    r"what (?:next|else)\b|"
    r"(?:does|did|is) (?:that|this|it|everything)\b[^?]*\b(?:help|make sense|clear|useful|better|enough)\b|"
    r"(?:is there )?anything (?:else|more|further)\b|"
    r"shall we (?:carry on|continue|move on|go on|keep going|press on|go further|do something else|try)\b|"
    r"ready (?:for|to)\b[^?]*\b(?:next|more|another|continue|go on|move on|carry on)\b|"
    r"(?:do you )?want (?:me )?to (?:keep going|continue|go on|carry on|move on|hear more|learn more|go deeper|dig deeper)\b|"
    r"(?:would you like|do you want|want) (?:another|more|some more|a (?:quiz|summary|recap|review)|"
    r"to (?:continue|hear more|learn more|go on|keep going|go deeper|dig deeper|explore|try another|see another|move on))\b"
    r")[^?]*\?\W*$", re.I)


def ack_question(sentence: str, asked: str) -> bool:
    """A closing filler question after an acknowledgement ("Right.", "That makes sense." -> "...What do you want to learn
    next?", "Does that help?"): he asked for nothing, so nothing is asked back. Only a closed set of fillers goes; every
    other question ("Are you sure you want to delete it?", "Shall I send it to Anna or Ben?", "Where should I save it?",
    "Which one did you mean?") needs his answer and stays."""
    from core import chatturn
    s = (sentence or "").replace("’", "'").strip()
    return (s.endswith("?") and chatturn.acknowledgement(asked or "")
            and bool(_ACK_FILLER.match(s)) and not _GENUINE.search(s))


def manual_closer(sentence: str) -> bool:
    """A trailing sentence of a tool-manual reply that only offers or hands the turn back (see _MANUAL_CLOSER), not one
    that asks for a detail this request needs."""
    s = (sentence or "").replace("\u2019", "'").strip()
    return bool(s) and bool(_MANUAL_CLOSER.search(s) or _ON_CALL.search(s)) and not _GENUINE.search(s)


def on_call(sentence: str) -> bool:
    """A sentence that presents Jarvis as a service waiting for orders, not a person in the conversation (see _ON_CALL)."""
    return bool(_ON_CALL.search((sentence or "").replace("’", "'").strip()))


# An offer to fetch his data from a place no skill reads (live 2026-11-21: "I don't have your latest scores on file. Want me to pull them
# from the phone app or run a quick check?": nothing reads scores from his phone). The source is real only when a loaded skill is named
# after it and reads or syncs it (garmin_sync, health_status); otherwise the offer names a capability Jarvis does not have.
_FETCH_OFFER = re.compile(r"\b(?:pull|fetch|grab|get|import|sync|bring (?:in|over)|load|download|read)\b[^.?!]{0,40}?\bfrom (?:the |your |my )?"
                          r"((?:[\w-]+\s+){0,2}?(?:phone|app|watch|tracker|account|profile|cloud|laptop|server|garmin|fitbit|strava|console|"
                          r"steam|xbox|playstation|psn|tablet|car))\b", re.I)
_OFFER_SHAPE = re.compile(r"\b(?:want me to|shall i|should i|would you like me to|do you want me to|i can|i could|i'?ll|let me)\b", re.I)
_READ_VERBS = ("sync", "status", "read", "get", "list", "pull", "import", "fetch")


def phantom_offer(sentence: str) -> bool:
    """An offer (or promise) to fetch his data from a source no loaded skill reads (see _FETCH_OFFER)."""
    s = (sentence or "").replace("’", "'")
    m = _FETCH_OFFER.search(s)
    if not m or not _OFFER_SHAPE.search(s):
        return False
    nouns = [w for w in re.findall(r"[a-z]+", m.group(1).casefold()) if w not in ("the", "your", "my", "app")] or ["app"]
    try:
        from tools import registry
        names = registry.names()
    except Exception:  # noqa: BLE001 - unknown: the offer is said as before
        return False
    if not names:
        return False                                  # no registry loaded: nothing to judge by
    # a generic source ("your profile", "your account") names no skill by itself: get_style_profile reads clothes, not Apex scores
    specific = [n for n in nouns if n not in ("profile", "account", "cloud", "phone", "server", "laptop")]
    if any(n in name and any(v in name for v in _READ_VERBS + ("health",)) for name in names for n in specific):
        return False
    # what is fetched may have its own skill whatever the source is called ("your calendar from your Google account": calendar_events)
    wanted = [w for w in re.findall(r"[a-z]{4,}", s[:m.start(1)].casefold())
              if w not in ("want", "would", "like", "shall", "should", "could", "pull", "fetch", "grab", "import", "sync", "bring",
                           "load", "download", "read", "them", "those", "that", "this", "your", "from", "latest", "quick")]
    return not any(w.rstrip("s") in name for name in names for w in wanted)


def chat_offer(sentence: str, asked: str = "") -> bool:
    """A sentence of a chat reply that offers a task he did not ask for: "Want me to...", "Shall I check...", "Should I
    keep going?", "Anything ... you want me to ...", "What can I do for you?", "Let me know if...", "I'm here if you
    need me." Not when his own line asked what Jarvis can do, for help or for a suggestion, and never a question that is
    conversation ("Which part went wrong?", "May I ask what happened?", "Do you need a break?", "What's next for it?")."""
    s = (sentence or "").replace("’", "'").strip()
    if not s or _ASKED_FOR_OFFER.search((asked or "").replace("’", "'")):
        return False
    return bool(_CHAT_OFFER.search(s) or _ON_CALL.search(s))


def _pure_offer(sentence: str) -> bool:
    """A sentence that IS a closing offer ("Anything else?", "Would you like me to look into it further?"), not content
    with an offer tacked on ("It's sunny, anything else?" keeps its content), never a real choice ("Which drive?") and
    never a question about a step ("Want me to go ahead and restart it?": the persona asks first, his yes needs it)."""
    from core import intelligence
    s = sentence.strip()
    m = _CLOSING_OFFER.search(s) if s.endswith("?") else None
    if not m or intelligence._NOT_AN_OFFER.search(s):
        return False
    lead = re.findall(r"[a-z']+", s[:m.start()].casefold())
    return all(w in _OFFER_LEAD for w in lead)


def _a_step(sentence: str) -> bool:
    """A question about a step he must say yes to first (_ASK_FIRST), judged clause by clause: a clause that only puts
    something in his notes, log, journal or list is none ("Shall I send that fact to your notes?"), while "Shall I close
    it and add a note to your log?" still asks about closing it."""
    return any(_ASK_FIRST.search(part) and not _KEEP_TARGET.search(part)
               for part in re.split(r",|\b(?:and|then)\b", sentence))


def unasked_offer(sentence: str, asked: str) -> bool:
    """An offer of something he did not ask for, after an answer to his question: to keep what was just said ("Shall I
    save that fact?") when his words asked to keep nothing, or to open something to show it ("Shall I open a map to show
    it?") when they asked to see nothing. Not after something he stated or decided ("Let's call my new garage project
    Falcon." -> "Shall I start a project log for it now?") or asked to have done ("Can you remind me to call Marcus at 5?"
    -> "Shall I set a reminder for 5 PM?"), never a step to ask first about or one the persona offers, and with no words
    of his to judge by, never."""
    from core import intelligence
    said = (asked or "").strip()
    s = sentence.strip()
    if not said or not s.endswith("?") or not _a_question(said) or _DO_IT.match(said):
        return False
    if _a_step(s) or _PERSONA_OFFER.search(s):
        return False        # a step he must say yes to first ("restart it and keep an eye on it?"), or the persona's own
    return bool((_KEEP_IT.search(s) and not intelligence._SAVE_CUES.search(said))
                or (_SHOW_IT.search(s) and not _SEE_CUES.search(said))
                or (_LESSON_PAGE.search(s) and _ANY_OFFER.search(s) and not _EXPLAIN.search(said)))


def drags_in(sentence: str, asked: str, said=()) -> bool:
    """An offer about something else: nothing in it is about his latest message or the answer said before it (`said`),
    and it is no step to ask first about. One that refers back ('it', 'that') is about what he said or decided, or, after
    his question, about the answer when it is short ('Want me to read it to you?'); one with a topic of its own after his
    question is not, whatever pronoun it has ("Shall I have a look at your pantry to see if we can use up something
    before it spoils?" after a synonym for happy, eval 2026-11-10). A choice ("Should we go with chicken or beef?") is
    never one, nor a question about what he would like after he asked for advice ("Would you like something quick?"
    after "What should I make for dinner?"), nor the question back after he asked to have something done ("Can you play
    some music?" -> "Shall I put on your usual playlist?"), nor an offer to find out after the answer admitted it does
    not know ("I don't have their prices yet. Want me to check them?"). With no words of his, never."""
    from core import intelligence
    s = sentence.strip()
    if (not (asked or "").strip() or _DO_IT.match(asked.strip()) or not s.endswith("?") or not _ANY_OFFER.search(s)
            or _a_step(s)):
        return False
    if intelligence._NOT_AN_OFFER.search(s) or (_PREFERENCE.search(s) and _ADVICE.search(asked)):
        return False                                    # a real question: the reply's point, his answer needs it
    if _PERSONA_OFFER.search(s):
        return False                                    # the persona's own next step (clipboard, measurements...)
    if _LESSON_PAGE.search(s) and _EXPLAIN.search(asked):
        return False                                    # the persona's own offer, after 'explain' / 'teach me'
    if any(_UNKNOWN.search(str(x).replace("’", "'")) for x in said or ()):
        return False                                    # it does not know yet: finding out is the way forward
    words = _content(s)
    if not words or (_REFERS.search(s) and (len(words) <= 2 or not _a_question(asked))):
        return False
    before = " ".join(said or ())
    return not (words | _caps(s)) & (_content(asked) | _content(before) | _caps(asked) | _caps(before))


def latest_only(asked: str) -> str:
    """The note for one more try when a reply answered an earlier question instead of his latest one (only in that request,
    never in the history)."""
    words = " ".join(str(asked or "").split())
    if len(words) > 200:
        words = words[:200].rsplit(" ", 1)[0] + "…"
    # Worded so: "An earlier answer is not the answer to it." drew a recap of the earlier answers once (eval 2026-11-10),
    # and "Reply to that alone, as if it were the first thing they said." told a follow-up to forget what it follows on
    # from (review 2026-11-11). Sandboxed probe on qwen3.5:9b with the whole conversation shown (2026-11-11): 7 of 8
    # retries for a copied answer answered the new question, and 7 of 7 for a follow-up wrongly held (the jacket after
    # the weather, the Eiffel Tower after France) kept what they follow on from.
    return f"(Reply to their latest message: \"{words}\". Answer what it asks, not an earlier question.)"


def _same_question(a: str, b: str) -> bool:
    """Two messages asking the same thing: most of their content words shared ("What's the weather?" / "What's the weather
    like?"), or, with no content words to go by, read alike ("Why are you here?" / "What are you for?"). Not the shared
    template of "How many minutes are in a day?" / "How many days are in a leap year?"."""
    ca, cb = _content(a), _content(b)
    if ca and cb:
        return len(ca & cb) / len(ca | cb) >= 0.5
    return _similar(a, b) >= SAME_QUESTION


# Asking to be told something, worded as a request ("Give me one tip for sleeping better.", "Tell me a fun fact").
_TELL_ME = re.compile(r"^\W*(?:(?:hey|ok|okay|so|um|uh|and)\W+)?(?:jarvis\W+)?(?:give me|tell me|name|suggest|recommend|"
                      r"teach me|explain|describe)\b", re.I)


def _a_question(said: str) -> bool:
    """Did he ask something (a question, or to be told something), rather than state or decide something?"""
    from core import intelligence
    text = (said or "").strip()
    return text.endswith("?") or intelligence.just_asking(text) or bool(_TELL_ME.match(text))


# A suggestion worded as a question is an answer ("How about a quick stir-fry with chicken thighs, sir?"), not an offer.
_SUGGESTION = re.compile(r"^\W*(?:how|what) about\b(?! (?:i|we|me)\b)", re.I)


def _lead_in(sentence: str) -> bool:
    """'One sec, sir.', 'Checking your plan.' (intelligence.is_lead_in)."""
    from core import intelligence
    return intelligence.is_lead_in(sentence)


def _says_nothing(sentence: str) -> bool:
    """A lead-in or an acknowledgment ('One sec, sir.', 'Certainly, sir.', 'Sure thing, sir.'): nothing said yet about
    WHAT he asked. A number or a negation says something ('One, sir.', 'No, sir.', 'Not at all, sir.' are answers); a
    number word only when it is all there is ('Good one, sir.' is an acknowledgment, review 2026-11-11)."""
    from core import intelligence
    words, (numbers, negations) = _content(sentence), _facts(sentence)
    return (intelligence.is_lead_in(sentence) or bool(_ACK_PHRASE.match(sentence.strip()))
            or (not (words - _ACKNOWLEDGES) and not negations and not (numbers and words <= _NUMBER_WORDS)))


def _asks_him(sentence: str) -> bool:
    """A sentence that asks him something (an offer, a choice), as opposed to telling or suggesting something."""
    s = sentence.strip()
    return s.endswith("?") and not _SUGGESTION.match(s)


# ------------------------------------------------------------------------------------- the spoken length budget
# Owner 2026-11-21: "shorter, better answers". Of his cut-off or corrected replies Nov 8-21 (155 hand-labelled from
# data/jarvis.log) 28 were too long, 18 ended on an offer or menu, 14 opened on filler, 7 restated his words. The 9B model
# keeps inventing new wordings, so this is a STRUCTURAL rule, not a phrase list: a spoken reply stops after a few
# sentences unless he asked for more, and what is left is kept for "tell me more" / "go on" (brain.llm Brain._resume).
SPOKEN_BUDGET = os.getenv("JARVIS_SPOKEN_BUDGET", "on").strip().lower() not in ("0", "off", "false", "no")
CHAT_BUDGET = (2, 40)       # content sentences, words: a remark, a quick fact, a yes or no
DEPTH_BUDGET = (3, 60)      # a why / how / what-do-you-think question (core.chatturn.depth)
# He asked for the long form: an explanation, steps, a recipe, a list, a plan, every one of something, a count of
# sentences. Then nothing is trimmed.
_DETAIL_ASKED = re.compile(
    r"\b(?:explain|explaining|in (?:more )?detail|detailed|walk me through|step[- ]by[- ]step|steps?|break (?:it|that|this) down|"
    r"tell me (?:more|everything|all)|everything (?:you|about)|all (?:of )?(?:your|the|its|their|them)|list|recipes?|ingredients|"
    r"how (?:do|can|should|would) (?:i|we)|how to|instructions?|teach me|go deeper|elaborate|the whole|full (?:story|list|answer|picture)|"
    r"every (?:one|single|step|detail|way)|summar(?:y|ise|ize)|itinerary|options|skills|abilit(?:y|ies)|capabilit(?:y|ies)|"
    r"(?:what|things?) (?:else )?(?:can )?you can do|can you do|"
    r"what are you able|in \w+ sentences|\w+ sentences|paragraph|plan (?:for|my|a|out|the)|compare|pros and cons|"
    r"story|stories|poem|essay|e-?mail|letter|draft|compose|write|describe|translate|tell me about|rundown|overview)\b", re.I)
# A sentence that carries a warning is said whatever the budget: a risk he must hear before he acts.
_RISK = re.compile(
    r"\b(?:careful|caution|warn(?:ing)?|risk(?:y|s)?|danger(?:ous)?|unsafe|harm(?:ful)?|can(?:'|no)t be undone|cannot be undone|"
    r"undo|permanent(?:ly)?|irreversibl[ey]|for good|lose (?:all|every|your|the)|wipes?|erase[sd]?|back(?:ed)? (?:it|them|that|everything) up|"
    r"backups?|allerg\w*|emergency|911|poison\w*|toxic|overdose|fire|shock|scam|phishing|not safe|never (?:share|run|give)|"
    r"delet\w*|overwrit\w*|format(?:s|ted|ting)?|bleach|doses?|medications?|unplug\w*|(?:will|would|could) break|brick\w*|"
    r"make sure|before you)\b", re.I)


# A question that asks for a detail of what he is after ("How far are you going?", "What's your budget?", "Drive or fly?"):
# never dropped as a question back, whatever _GENUINE misses.
_CLARIFIES = re.compile(r"\b(?:how (?:far|much|many|long|old|big|often|soon|early|late)|where|when|who|whose|which|"
                        r"what(?:'s| is| are) (?:your|the) (?:budget|name|address|date|time|plan|goal|size|price)|"
                        r"\w+ or \w+\?)", re.I)


def spoken_budget(asked: str):
    """(sentences, words) a spoken reply to his line may run to, or None: no budget (he asked for detail, the switch
    JARVIS_SPOKEN_BUDGET=off, or no words of his to judge by)."""
    text = " ".join(str(asked or "").replace("’", "'").split())
    if not SPOKEN_BUDGET or not text or _DETAIL_ASKED.search(text):
        return None
    from core import chatturn
    return DEPTH_BUDGET if chatturn.depth(text) else CHAT_BUDGET


def risk(sentence: str) -> bool:
    return bool(_RISK.search((sentence or "").replace("’", "'")))


# A sentence that only says his request back to him before answering it ("You want me to look at your setup and make it
# smarter, right?", "I see you're heading to your mother's in Brentwood on Tuesday."): its words are his.
_RESTATE = re.compile(
    r"^\W*(?:(?:so|okay|ok|right|alright|all right|got it|understood|sir)[,.!\s]+)*(?:i see (?:that )?|it sounds like |sounds like |"
    r"i hear (?:that )?|i understand (?:that )?|i get (?:it|that)[,\s]+|so )?you(?:'re| are|'d| would|'ve| have)? (?:want(?:ing)?|"
    r"asking|looking to|wondering|saying|heading|planning|going|trying|thinking|feel(?:ing)?|mentioned|like to|got)\b", re.I)


def restates(sentence: str, his_words: str) -> bool:
    """Is this sentence his own words said back (the shape above, at least 60% of its content words his, no number or
    negation of its own and no warning)? 'You're looking at about six hours of driving.' is an answer: its number is new."""
    s = (sentence or "").replace("’", "'").strip()
    if not _RESTATE.match(s) or risk(s):
        return False
    words = _content(s) - {"want", "ask", "look", "wonder", "say", "plan", "go", "try", "think", "feel", "mention", "like",
                           "see", "sound", "hear", "understand", "right", "sir", "get", "got"}
    his = _content(his_words)
    if not words or not his:
        return False
    numbers, negations = _facts(s)
    his_numbers, his_negations = _facts(his_words)
    if set(numbers) - set(his_numbers) or (negations and not his_negations):
        return False
    # every name in it is one of his words ("You mentioned your sister's wedding is in Brentwood." is a recall answer when
    # he never said Brentwood)
    his_plain = set(re.findall(r"[a-z0-9']+", (his_words or "").casefold().replace("\u2019", "'")))
    names = {w.casefold() for w in re.findall(r"(?<=\s)[A-Z][\w']*", s)} | _caps(s)
    if any(n not in his_plain and n.removesuffix("'s") not in his_plain for n in names):
        return False
    return len(words & his) / len(words) >= 0.8


class Polisher:
    """Feed it a reply's sentences in order; it passes them on to `out` as JARVIS would say them:
      - 'the owner' who asked JARVIS something becomes 'you' when the owner is the one addressed;
      - a reply that repeats the previous one (REPEAT_SIMILARITY, the same numbers and negations) says its first copied
        sentence as 'As I said, sir: ...' only when the copy OPENS the reply and he asked the same thing again (or what he
        asked is not known); the rest of the copy, and a copy after something new was said, is not said again (unless he
        asked to hear it again). Never once a tool ran this turn (`fresh()`): a new reading, or a second action's result,
        is news even when it reads like the last reply;
      - an answer to an EARLIER, different question (`earlier`: his messages and Jarvis's replies, oldest first) that
        opens the reply is not said: with `retry`, nothing more of the reply is said and `stale` is set (the Brain asks once
        more, with latest_only(), or says the draft after all with release() / give_back()); without, it is said as
        written (never dead air). After something new it is dropped. A draft whose rest answers his latest message only
        restated what it builds on, and is said (settle());
      - a closing offer that proposes nothing, or one of something he did not ask for (unasked_offer, drags_in), is held
        back one sentence: said in order if more follows, dropped when the reply ends on it after something was said
        about what he asked, kept when nothing was (only 'Certainly, sir.') or a question is really waiting (`keep()`,
        e.g. a pending approval).
    `edits` lists (original, replacement) so the stored reply can be made to equal what was said. finish() at the end."""

    def __init__(self, out, previous: str = "", *, owner: bool = True, title: str = "sir", asked: str = "",
                 english: bool = True, keep=lambda: False, fresh=lambda: False, earlier=(), retry: bool = False,
                 chat=lambda: False, unknown=lambda sentence: False, thin_title: bool = False, budget=None,
                 withheld=()):
        # (sentences, words) this reply may run to when spoken (spoken_budget), or None. Past it, what is left is kept in
        # `trimmed` (said on "tell me more"), never said and never stored as said; a warning or a question his answer
        # needs is still said. Only while no tool ran and no question waits (keep()).
        self.budget = budget
        # what the budget kept back of the LAST reply: he never heard it, but a draft that says it again is still that
        # reply over again (the loop guard, _said_last_time), not news
        self.withheld = [s for s in withheld or () if str(s).strip()]
        self.out, self.owner, self.title, self.english, self.keep, self.fresh = out, owner, title, english, keep, fresh
        # Does this sentence mean he has nothing to answer from (he says so, or pretends to have looked)? The Brain's
        # judgement for the line being answered; a draft that OPENS on such a sentence is held whole (`lookup`), like a
        # stale one, and the Brain looks the answer up on the internet (Brain._look_it_up) before anything is said.
        self.unknown = unknown
        self.lookup = False
        # Is this reply being written on the chat path (core.chatturn)? Asked per sentence: the path is chosen after the
        # Polisher is made. There, a sentence that picks up something he said earlier is the conversation, not a topic
        # dragged in (_dragged), so it is said.
        self.chat = chat
        exchanges = [(str(q or ""), str(r or "")) for q, r in (earlier or ())]
        if exchanges and not previous:
            previous = exchanges[-1][1]
        # the last two replies: his title is said again only when neither had it (about one reply in three, his choice 2026-11-14)
        self.recent_replies = [r for _q, r in exchanges[-2:]] if exchanges else ([previous] if previous else [])
        self.thin_title = thin_title           # the Brain's spoken replies only (brain/llm.py); unit callers keep every word
        self.before = [s for s in _SPLIT.split((previous or "").strip()) if len(s.split()) >= 6 and not s.endswith("?")]
        self.asked = " ".join((asked or "").split())
        self.again = bool(_ASKED_AGAIN.search(self.asked))
        self.back = bool(_REFERS_BACK.search(self.asked))
        # 'Go ahead.': his yes to what the last reply offered, never a different question
        self.accepts = _accepts(self.asked, exchanges[-1][1] if exchanges else previous)
        # what he said in the exchange the last reply answered (None: not known, as for a reply judged on its own)
        self.last_said = exchanges[-1][0] if exchanges and exchanges[-1][0].strip() else None
        self.last_reply = exchanges[-1][1] if exchanges else ""
        self.found: tuple[str, str] | None = None
        # His bare yes to a question the last reply asked ("Would you like me to open it now?"): that question is answered, so it
        # is not asked again (live 2026-11-14: the same question came back after his 'Yes.'). chatturn.binding_note tells the model.
        self.yes_to = ""
        try:
            from core import chatturn
            if self.last_reply and chatturn.bare_yes(self.asked):
                self.yes_to = chatturn._offer_in(self.last_reply) if chatturn._offer_in(self.last_reply).endswith("?") else ""
        except Exception:  # noqa: BLE001 - a guard only: without it the reply is judged as before
            self.yes_to = ""
        # the earlier QUESTIONS and their answers: what he stated himself ("Marcus is helping me") may well be said back
        self.questions = [(q, r) for q, r in exchanges[-OLDER_EXCHANGES:] if r.strip() and _a_question(q)]
        # ...and what he said that was no question ("Open. Control. Center."): its reply is never said back as the answer to a
        # short answer of his (that stays for questions), but the same sentences word for word are the old reply again
        # (live 2026-11-14: the same three sentences about step four, three times, for three different lines)
        # Only for a line of his that is no question either: "Who's helping me with it?" may be answered with what he stated.
        self.statements = ([(q, r) for q, r in exchanges[-OLDER_EXCHANGES:] if r.strip() and not _a_question(q)]
                           if not _a_question(asked) else [])
        # what each of his earlier messages was about, when it has nothing to do with his latest one (_dragged)
        latest = _content(self.asked)
        self.topics = [t for t in (_content(q) for q, _r in exchanges[-OLDER_EXCHANGES:]) if t and not t & latest]
        self.retry = retry
        # his last few lines: a sentence made of these words only says his request back (restates)
        self.his_recent = " ".join([q for q, _r in exchanges[-2:]] + [self.asked])
        self.heard: list[str] = []      # said before a draft held for one more try (an acknowledgment): not said twice
        self._held_draft: tuple | None = None
        self._reset()

    def _reset(self) -> None:
        self.trimmed: list[str] = []                  # past the spoken budget: kept for "tell me more"
        self.preamble: tuple[str, str] | None = None  # an opening that only restates his words, held until the answer comes
        self.said: list[str] = []
        self.edits: list[tuple[str, str]] = []
        self.held: tuple[str, str] | None = None      # (as written, as it would be said)
        self.copied = False                           # 'As I said' was said once this reply: the rest of the copy goes
        self.waiting: list[str] | None = None         # a draft opening on the last reply again, held to its end (_unplaced)
        self.settled = False                          # ...and released: something new followed, it is said as written
        self.looping = False                          # the one more try gave the old answer again: held, handed back (finish)
        self.reasked = ""                             # the draft asked again what his yes had just answered: dropped (its words)
        self.stale = False                            # the reply opened on an answer to an earlier question
        self.source: tuple[str, str] | None = None    # ...that exchange (his words, the reply)
        self.pending: list[str] = []                  # the rest of that draft, unsaid (release() says it after all)

    def once_more(self) -> None:
        """Before the Brain's one more try after `stale`: the reply starts over, with no further try. What was already
        said (an acknowledgment) is not said again, and the stored reply still starts with it."""
        if self.preamble is not None:                   # never said: not kept as said either
            self.edits.append((self.preamble[0], ""))
        self._held_draft = (list(self.said), list(self.edits), list(self.pending), self.source, list(self.trimmed))
        self.retry = False
        self._reset()
        self.heard = list(self._held_draft[0])

    def give_back(self) -> None:
        """The one more try got no answer at all (the local model failed): the held draft is said as written after all,
        never a failure note in place of an answer that was there (and never a paid call to replace a free one)."""
        if self._held_draft is None:
            return
        said, edits, pending, source, trimmed = self._held_draft
        self._held_draft, self.heard = None, []
        self._reset()
        self.said, self.edits, self.pending, self.source, self.stale = said, edits, pending, source, True
        self.trimmed = trimmed
        self.release()

    def release(self, instead=None) -> None:
        """No other try after all (a tool ran in the meantime, or he cut in): the held draft goes through as written,
        after what was already said, judged as it would have been without a retry. `instead(sentence)` may give an honest
        line for a sentence that must not be said as written (a claim of having looked when nothing was searched): that
        line is said, and the stored reply is made to say it too."""
        pending, self.pending = self.pending, []
        self.retry, self.stale, self.source, self.lookup = False, False, None, False
        for sentence in pending:
            honest = instead(sentence) if instead else None
            if honest and self.english:
                self._say(sentence, honest)
            else:
                self(sentence)

    def _say(self, original: str, sentence: str) -> None:
        sentence = self._thin_title(sentence)
        stored = sentence
        if self.heard and sentence.strip():             # the one more try's first words: stored after what he heard
            stored, self.heard = " ".join(self.heard + [sentence]), []
        if stored != original:
            self.edits.append((original, stored))
        self.said.append(sentence)
        self.out(sentence)

    def _thin_title(self, sentence: str) -> str:
        """His title ("sir") on about one reply in three: dropped from a sentence when this reply already said it or one of
        the last two replies had it (live log 2026-11-20: "sir" in 44% of the model's replies, and in every code line).
        Only the plain forms are taken out (", sir" before punctuation, a leading "Sir, "); any other use stays."""
        if not self.thin_title or not self.title or not self.english or not sentence:
            return sentence
        word = re.escape(self.title)
        mention = re.compile(r"\b" + word + r"\b", re.I)
        if not mention.search(sentence):
            return sentence
        if not any(mention.search(r or "") for r in [*self.said, *self.recent_replies]):
            return sentence
        thinned = re.sub(r",\s*" + word + r"(?=\s*[.!?,;:]|\s*$)", "", sentence, flags=re.I)
        # "Sir, the build is done." -> "The build is done." ("Sir, iPhone sales..." keeps its "iPhone")
        thinned = re.sub(r"^(\W*)" + word + r",\s*(\w)(\w?)",
                         lambda m: m.group(1) + (m.group(2).upper() if not m.group(3).isupper() else m.group(2)) + m.group(3),
                         thinned, flags=re.I)
        thinned = re.sub(r"\s*,\s*([,.!?;:])", lambda m: m.group(1), thinned)
        thinned = re.sub(r"\s+([,.!?;:])", lambda m: m.group(1), thinned)
        return thinned if thinned.strip(" .,!?") else sentence

    def _news(self) -> bool:
        """Something was said already this reply that is more than a lead-in or an acknowledgment ('Certainly, sir.'):
        'It's gracias, sir.' is an answer, three words or not."""
        return any(not _says_nothing(s) for s in self.said)

    def _other_question(self, said: str, last: bool = False) -> bool:
        """His latest message is not that earlier one again, nor a question about what was said before. One with no
        content word of its own ("And why?", "How so?", "Why are you here?") follows on from what was just said, and so
        does his yes to it ('Go ahead.', 'Sounds good.'), and one that opens on 'and' / 'so' / 'what about' (`last`: that
        earlier one was the last exchange)."""
        return (bool(self.asked) and not self.again and not self.back and not self.accepts and bool(_content(self.asked))
                and not (last and _FOLLOW_ON.match(self.asked)) and not _same_question(said, self.asked))

    def _tool_ran(self) -> bool:
        try:
            return bool(self.fresh())
        except Exception:  # noqa: BLE001 - when unsure whether it is news, it is news
            return True

    def _copy(self, sentence: str) -> bool:
        """A sentence of the previous reply said again (near word for word, the same numbers and negations)."""
        if not self.before or self.again or sentence.rstrip().endswith("?") or len(sentence.split()) < 6:
            return False
        facts = _facts(sentence)
        if not any(_similar(sentence, b) >= REPEAT_SIMILARITY and _facts(b) == facts for b in self.before):
            return False
        return not self._tool_ran()

    def _stale(self, sentence: str) -> bool:
        """An answer to an earlier, different question said again for this one: the same sentence near word for word, or a
        short answer made only of an earlier answer's words and none of his question's ('Gracias, sir.' for a synonym of
        happy), that earlier question sharing nothing with his latest one. The exchange it came from is kept (`found`).
        A follow-on ('And who runs SpaceX?' after Tesla) may be answered in the last answer's words ('Elon Musk, sir.'),
        never with the last answer said again near word for word ('What about tomorrow?' -> today's reading). The short
        answer counts only while nothing was said yet about what he asked: after news about it, a list restating earlier
        prices ('Rice: 2.95 at the discount store.') is the answer, not a copy, while after an opener ('Easy one, sir.',
        'The word is simple, sir.') the copied answer is still one (review 2026-11-11)."""
        self.found = None
        if not (self.questions or self.statements) or _asks_him(sentence):
            return False
        words, asked, facts = _content(sentence), _content(self.asked), _facts(sentence)
        if not words:
            return False
        short_ok = not any(not _says_nothing(s) and _content(s) & asked for s in self.said)
        for question, reply in self.questions + self.statements:
            if not self._other_question(question):
                continue
            follow_on = not self._other_question(question, last=(question, reply) == (self.last_said, self.last_reply))
            unrelated = (short_ok and not follow_on and not _content(question) & asked
                         and (question, reply) in self.questions)
            from_statement = (question, reply) in self.statements
            for old in _SPLIT.split(reply.strip()):
                if _asks_him(old):
                    continue
                theirs = _content(old)
                if from_statement:
                    # A reply to a line of his that was no question is repeated only when it is a LONG sentence nearly the same
                    # (0.9, 8 words or more) or any sentence word for word: 'Answer to the typed one, sir.' after 'Answer to the
                    # spoken one, sir.' is a templated answer to another line, not the old reply again (full gate 2026-11-15).
                    if ((len(sentence.split()) >= 8 and _similar(sentence, old) >= 0.9 and _facts(old) == facts)
                            or (len(_flat_words(sentence)) >= 3 and _flat_words(sentence) == _flat_words(old))):
                        self.found = (question, reply)
                        return not self._tool_ran()
                    continue
                if ((len(sentence.split()) >= 6 and _similar(sentence, old) >= REPEAT_SIMILARITY and _facts(old) == facts)
                        or (unrelated and not words & asked and words <= theirs and len(theirs) <= 2 * len(words) + 2)):
                    self.found = (question, reply)
                    return not self._tool_ran()
        return False

    def _dragged(self, sentence: str) -> bool:
        """After the answer, a sentence about an earlier topic of his and about nothing he just asked or was just told
        ("The first moon landing was in 1969, sir. And for dinner tonight, how about a simple stir-fry with chicken and
        veggies?", eval 2026-11-10; "Falcon is noted as your garage project." on every reply, copy_probe): two or more of
        its specific content words (four letters or more, or a number) come from one earlier message of his that has
        nothing to do with his latest one.
        Only after a question (review 2026-11-11): after 'No, you did not.' or a garbled line, what came before IS the
        topic, and the sentence restating it is the answer. After a follow-on ('And I can see it in the app?') or a
        question of one content word ('Did it work?') only what goes on with his last exchange is kept: 'What time is
        it?' or 'And what's the capital of France?' still loses the Falcon line (review 2026-11-11, round 4). Never a
        question about a step ('Shall I go ahead and delete the old backups?'), nor anything while a question is really
        waiting (`keep()`), nor what goes on with the last reply when his question does too."""
        if not self.topics or self.again or self.back or not self._news():
            return False
        try:
            if self.chat():
                return False    # a chat reply: recalling what he told him earlier is wanted, never cut (2026-11-12)
        except Exception:  # noqa: BLE001 - unsure which path: judged as before
            pass
        latest = _content(self.asked)
        if not latest or not _a_question(self.asked):
            return False
        if _asks_him(sentence) and (_a_step(sentence) or _PERSONA_OFFER.search(sentence)):
            return False        # a step he must say yes to first, or the persona's own offer
        words, said = _content(sentence), " ".join(self.said)
        if not words or (words | _caps(sentence)) & (latest | _content(said) | _caps(self.asked) | _caps(said)):
            return False
        if ((len(latest) < 2 or _FOLLOW_ON.match(self.asked))
                and words & _content(f"{self.last_said or ''} {self.last_reply}")):
            return False        # his follow-on goes on with his last exchange, and so does this
        last = _content(self.last_reply)
        if latest & last and words & last:
            return False        # he goes on with the last reply, and so does this: what it was about is still the topic
        if _TIES_BACK.search(sentence) and _ADVICE.search(self.asked):
            return False        # advice tied to what he said earlier ("No mushrooms in it, and it's vegetarian.")
        if not any(len({w for w in words & topic if len(w) >= 4 or any(c.isdigit() for c in w)}) >= 2
                   for topic in self.topics):
            return False        # 'get' + 'son', 'one' + 'open' are no earlier topic
        return not self.keep() and not self._tool_ran()

    def _as_i_said(self, sentence: str) -> str:
        """'As I said, sir: ...', without a second 'sir' when the sentence already has one."""
        title = (self.title or "").strip()
        if not title or re.search(rf"\b{re.escape(title)}\b", sentence, re.I):
            return f"As I said: {sentence}"
        return f"As I said, {title}: {sentence}"

    def _unplaced(self) -> bool:
        """The last reply asked him something, he answered in a few words, and the draft opens on that same reply again
        (live 2026-11-14: "The future." three times, each got "I see WhatsApp Web on your screen. Can you tell me its
        name?", the third with "As I said, sir"). Not when he asked to hear it again or said yes to an offer."""
        asked = self.asked.strip(" .!?,")
        if not asked or len(asked.split()) > 6 or self.again or self.accepts or _DECLINES.match(asked):
            return False          # ("No." to "Shall I...?" is an answer, never something he could not place)
        # judged on the last reply as WRITTEN (what the spoken budget kept back included), as before the budget: a draft
        # that is that reply again gets the one more try (_stale), not the "cannot place it" line
        written = " ".join([self.last_reply.strip(), *self.withheld]).strip()
        last = _SPLIT.split(written)[-1] if written else ""
        # a real question about what he said, never the "Yes, sir?" that answers his name (replay of his log: an order
        # given twice after it, "Start Gaming Coach.", earned the same result twice and is no misunderstanding)
        return last.endswith("?") and len(last.split()) >= 4

    def _said_last_time(self, sentence: str) -> bool:
        """A sentence of the last reply (its question included), near word for word."""
        return any(_similar(sentence, b) >= REPEAT_SIMILARITY
                   for b in [*_SPLIT.split(self.last_reply.strip()), *self.withheld] if b.strip())

    def _said_before(self, sentence: str) -> bool:
        """A sentence (a question too) of one of the recent replies, near word for word."""
        return any(_similar(sentence, old) >= REPEAT_SIMILARITY
                   for old in [*(o for _q, reply in (self.questions + self.statements)[-OLDER_EXCHANGES:]
                                 for o in _SPLIT.split(reply.strip())), *self.withheld] if len(old.split()) >= 3)

    _NON_ANSWER = re.compile(r"lost my train of thought|say it once more|couldn'?t work out how|could you say (?:that|it) (?:once|again)|"
                             r"i keep giving you the same answer", re.I)

    def _unhelpful_loop(self, held) -> bool:
        """Is this repeat a LOOP, as opposed to the right answer to a line that asks for it in other words? The same non-answer
        ('I lost my train of thought', 'I couldn't work out how...'); or the reply asks him something again and his line is no new
        question; or it was already said twice before and his line asks no new question. 'What's your name?' after 'Who are you?',
        'Cheers Jarvis.' after 'Thanks.', 'You good?' after 'How are you?' get their answer as written (review round 2)."""
        text = " ".join(held).replace("’", "'")
        if self._NON_ANSWER.search(text):
            return True
        new_question = _a_question(self.asked) and (self.last_said is None or not _same_question(self.last_said, self.asked))
        if new_question:
            return False
        said_before = sum(1 for _q, r in (self.questions + self.statements)
                          if any(_similar(h, o) >= REPEAT_SIMILARITY for h in held for o in _SPLIT.split(r.strip()) if len(o.split()) >= 3))
        return said_before >= 2 or any(_asks_him(h) for h in held)

    def _loop_line(self) -> str:
        title = (self.title or "").strip()
        return (f"I keep giving you the same answer, {title}, and it isn't helping. Tell me the one thing you want done next."
                if title else "I keep giving you the same answer, and it isn't helping. Tell me the one thing you want done next.")

    def _repair(self) -> str:
        heard = self.asked.replace('"', "").strip(" .!?,'")
        title = (self.title or "").strip()
        return (f"I heard \"{heard}\", {title}, and I cannot place it. Say it another way." if title
                else f"I heard \"{heard}\", and I cannot place it. Say it another way.")

    def __call__(self, sentence: str) -> None:
        original = sentence
        if self.waiting is not None:
            if (not sentence or not sentence.strip() or self._said_last_time(sentence)
                    or (self.looping and self._said_before(sentence))):
                self.waiting.append(original)           # still only the last reply over again
                return
            # something new follows ("...Opening it now."): the draft answers him after all and is said as written
            held, self.waiting, self.settled = self.waiting, None, True
            for earlier in held:
                self(earlier)
        if self.stale:                                 # the Brain asks once more: nothing more of this draft is said
            self.pending.append(original)
            return
        if not sentence or not sentence.strip() or not self.english:
            self._say(original, sentence)
            return
        if (self.retry and not self.lookup and not self.said and not self.heard and self.waiting is None
                and self.held is None and not self._tool_ran() and self.unknown(sentence)):
            self.stale, self.source, self.lookup = True, None, True     # nothing of this draft is said: it is looked up
            self.pending.append(original)
            return
        if self.held is not None:                       # more follows the offer: it was mid-reply, said in order
            held, self.held = self.held, None
            if self._on_call(held[1]):
                self.edits.append((held[0], ""))        # a help-desk line before real words: never said (sandbox 2026-11-21,
            elif not self._over_budget(*held):          # "I'm here as your partner, sir. I'll handle things with care...")
                self._say(*held)
        if self.said and self._on_call(sentence):
            self.edits.append((original, ""))           # ...nor after them
            return
        if self.said and self.english and not self._tool_ran() and phantom_offer(sentence):    # never dead air
            self.edits.append((original, ""))           # an offer of a skill he does not have is never made
            return
        if (self.heard and not self.said and not sentence.rstrip().endswith("?")
                and (_lead_in(sentence) or any(_similar(sentence, h) >= REPEAT_SIMILARITY for h in self.heard))):
            # the one more try opens on a lead-in, or on the acknowledgment already said: he hears it once ("Certainly,
            # sir. [the retry] Certainly, sir. Glad is one."), and the stored reply keeps the one he heard. A short
            # answer is never taken for one ('No, sir.', 'One, sir.', 'Happy, sir.': review 2026-11-11).
            self.edits.append((original, " ".join(self.heard)))
            self.heard = []
            return
        if self.yes_to and sentence.rstrip().endswith("?") and _similar(sentence, self.yes_to) >= 0.7 and not self._tool_ran():
            self.edits.append((original, ""))           # his yes already answered it: never asked again
            self.reasked = original
            return
        if self.owner:
            sentence = second_person(sentence)
        if not self.said and echoes_ack(sentence, self.asked):
            self._say(original, other_ack(self.asked, self.title, self.last_reply))    # "Right." to "Right.": never his word back
            return
        if not (self.settled or self.said or self.copied) and self._unplaced() and self._copy(sentence):
            # His short answer to the question just asked earns the same reply again. Held to the end of the draft: if
            # it is nothing but the last reply over again, one line says what was heard and hands it back (finish()).
            self.waiting = [original]
            return
        stale = self._stale(sentence)
        source = self.found
        copy = not stale and self._copy(sentence)
        if (copy and not (self.copied or self._news()) and self.last_said is not None
                and self._other_question(self.last_said) and not _content(sentence) & _content(self.asked)):
            # (strict, a follow-on included: 'What about tomorrow?' is not answered by the last reply word for word)
            stale, source = True, (self.last_said, self.last_reply)   # the last reply's line opening a new answer
        if stale:
            if self._news():
                self.edits.append((original, ""))       # new content was said: the old answer is not added to it
                return
            if self.retry:
                self.stale, self.source = True, source
                self.pending.append(original)
                return                                  # (without a retry it is said: never dead air)
            if self._held_draft is not None and not self.said and not self.settled:
                # The one more try wrote the old answer again. Said a third time it is the loop he heard on 2026-11-14; it is
                # held to the end of the draft and, if nothing new follows it, one line says so and hands the turn back (finish).
                self.waiting, self.looping = [original], True
                return
        elif copy:
            if self.copied or self._news():
                self.edits.append((original, ""))       # the rest of the copy, or a copy after news, is not said twice
                return
            self.copied = True                          # a copy that opens the reply is said once: never dead air
            if self.last_said is None or not self._other_question(self.last_said):
                sentence = self._as_i_said(sentence)    # he asked the same again (or that is not known)
        elif self._dragged(sentence):
            self.edits.append((original, ""))           # an earlier topic of his, tacked on after the answer
            return
        if (_pure_offer(sentence) or unasked_offer(sentence, self.asked) or self._chat_offer(sentence) or self._manual_closer(sentence)
                or ack_question(sentence, self.asked) or (drags_in(sentence, self.asked, self.said) and not self._asks_leave(sentence))
                or self._question_back(sentence)):
            self.held = (original, sentence)
            return
        if (self.preamble is None and not self.said and not self.heard and self.budget is not None
                and not self._recall_asked() and restates(sentence, self.his_recent) and not self.keep()
                and not self._tool_ran()):
            self.preamble = (original, sentence)        # said only if nothing else comes (finish)
            return
        if self._over_budget(original, sentence):
            return
        if self.preamble is not None:                   # the answer came: his words said back to him are not
            self.edits.append((self.preamble[0], ""))
            self.preamble = None
        self._say(original, sentence)

    def _recall_asked(self) -> bool:
        """He asked about his own things or what he told before: an answer opening on his words is the answer."""
        try:
            from core import chatturn, intelligence
            return bool(chatturn.wants_recall(self.asked) or intelligence.about_them(self.asked))
        except Exception:  # noqa: BLE001 - unsure: no opening is held
            return True

    def _question_back(self, sentence: str) -> bool:
        """With the budget on: a question asked back after he asked something and was answered ("What do you have in
        mind?", "Which part do you like best?"). Held like an offer: said if more follows, dropped if the reply ends on
        it (character sheet: when you have answered, stop). Never a question his answer needs (_GENUINE: which one, did
        you mean, what time...), a step to say yes to, a warning, nor once a tool ran or while a question waits."""
        s = sentence.replace("’", "'").strip()
        if self.budget is None or not s.endswith("?") or not self._news() or not _a_question(self.asked):
            return False
        if _GENUINE.search(s) or _CLARIFIES.search(s) or _a_step(s) or risk(s) or _SUGGESTION.match(s):
            return False
        try:
            return not self.keep() and not self._tool_ran()
        except Exception:  # noqa: BLE001 - unsure: said as before
            return False

    def _over_budget(self, original: str, sentence: str) -> bool:
        """Past the spoken budget: the sentence goes to `trimmed` (and out of the stored reply). Counted are sentences that
        say something (not 'Got it, sir.'); the first one is always said, however long. Never a warning (risk), never a
        question about a step or a choice his answer needs, never once a tool ran or while a question waits."""
        if self.budget is None or not self.english or not sentence.strip():
            return False
        try:
            if self.keep() or self._tool_ran():
                return False                            # a tool's report, or a question waiting: always said
        except Exception:  # noqa: BLE001 - unsure: said as before
            return False
        if self.trimmed:                                # trimming began: the rest follows in order
            if risk(sentence) or (sentence.rstrip().endswith("?") and (_GENUINE.search(sentence) or _a_step(sentence))):
                return False
        else:
            content = [s for s in self.said if not _says_nothing(s)]
            if not content or _says_nothing(sentence):
                return False
            most, words = self.budget
            if len(content) < most and sum(len(s.split()) for s in content) + len(sentence.split()) <= words:
                return False
            if risk(sentence) or (sentence.rstrip().endswith("?") and (_GENUINE.search(sentence) or _a_step(sentence))):
                return False
        self.edits.append((original, ""))
        if not sentence.rstrip().endswith("?") and not (_pure_offer(sentence) or chat_offer(sentence, self.asked)
                                                        or manual_closer(sentence)):
            self.trimmed.append(sentence)               # an offer or a question back is never kept for "tell me more"
        return True

    def _on_call(self, sentence: str) -> bool:
        """A help-desk line (on_call) this turn can do without: not while a question really waits, not after a tool ran, and on a
        command a question ("What do you want me to do now?") stays."""
        try:
            return (self.english and on_call(sentence) and not self.keep() and not self._tool_ran()
                    and (bool(self.chat()) or not sentence.rstrip().endswith("?")))
        except Exception:  # noqa: BLE001 - unsure: said as before
            return False

    def _manual_closer(self, sentence: str) -> bool:
        """On the tool-manual path (not chat): a trailing sentence that only hands the turn back."""
        try:
            if self.chat():
                return False
            # his bare one- or two-word fragment ("Streaming.", "Protocol."): nothing was asked, so nothing is offered either
            # (sandbox 2026-11-21: "...Want me to check what's new on streaming right now?" both times)
            if manual_closer(sentence):
                return True
            from core import chatturn
            fragment = (len(re.findall(r"[\w']+", self.asked)) == 1 and not self.asked.rstrip().endswith("?")
                        and not chatturn.acknowledgement(self.asked) and not _DECLINES.match(self.asked.strip()))
            return (fragment and chat_offer(sentence, self.asked) and not _GENUINE.search(sentence)
                    and not _REFERS.search(sentence))
        except Exception:  # noqa: BLE001 - unsure which path: judged as before
            return False

    def _chat_offer(self, sentence: str) -> bool:
        """On the chat path (nothing can be done in such a reply): a sentence that offers or asks what to do next."""
        try:
            return bool(self.chat()) and chat_offer(sentence, self.asked)
        except Exception:  # noqa: BLE001 - unsure which path: judged as before
            return False

    def _asks_leave(self, sentence: str) -> bool:
        try:
            return bool(self.chat()) and bool(_ASKS_LEAVE.search(sentence.replace("’", "'")))
        except Exception:  # noqa: BLE001 - unsure which path: judged as before
            return False

    def settle(self) -> None:
        """At the end of the draft: a reply that is nothing but an offer he did not ask for, to his question ("Shall I save
        that name to the project log, sir?" for a gray cat's name, eval 2026-11-10), answers nothing. With `retry` it is
        held like a stale answer (the Brain asks once more; release() would still say it as the whole reply). An
        acknowledgment before it ('Certainly, sir.') answers nothing either.
        A draft held because it opened on an earlier answer stays held even when its rest is about his latest message
        ("It's 62 degrees and sunny, sir. A light jacket is plenty."): the one more try sees the whole conversation and is
        a free local call, while a filler sentence that only names his topic ('Happy to help with more phrases.') would
        let the old answer through (review 2026-11-11)."""
        if (self.retry and not self.stale and self.held is not None and not self._news() and _a_question(self.asked)
                and not _pure_offer(self.held[1]) and not self.keep()):
            self.stale, self.source = True, None
            self.pending.append(self.held[0])
            self.held = None

    def finish(self) -> None:
        if self.preamble is not None:                   # nothing came after it: it is the reply
            (original, sentence), self.preamble = self.preamble, None
            self._say(original, sentence)
        if self.reasked and not self._news() and self.held is None and self.waiting is None:
            original, self.reasked = self.reasked, ""   # only that question came: one line, and the turn is his
            self.edits = [e for e in self.edits if e != (original, "")]
            title = (self.title or "").strip()
            self._say(original, f"I couldn't do that one, {title}, and I won't ask you again. Tell me exactly what to do and I'll do it."
                      if title else "I couldn't do that one, and I won't ask you again. Tell me exactly what to do and I'll do it.")
            return
        if self.waiting is not None:                    # the whole draft was the last reply again: handed back once
            held, self.waiting = self.waiting, None
            if self._tool_ran():                        # something was done meanwhile: the draft is its report
                self.settled = True
                for earlier in held:
                    self(earlier)
                return self.finish()
            if self.looping and not self._unhelpful_loop(held):
                # the old answer to a line that asks for it in other words ("What's your name?" after "Who are you?"): said as written
                self.looping, self.settled = False, True
                for earlier in held:
                    self(earlier)
                return self.finish()
            self._say(held[0], self._loop_line() if self.looping else self._repair())
            self.looping = False
            self.edits.extend((rest, "") for rest in held[1:])
            return
        if self.held is None:
            return
        (original, sentence), self.held = self.held, None
        # A closing offer goes after anything ('Done, sir.' is the whole reply); an offer of something unasked only after
        # something about what he asked: 'Certainly, sir.' alone answers nothing, the offer is kept rather than dead air.
        if (self.said if (_pure_offer(sentence) or self._chat_offer(sentence) or self._manual_closer(sentence)
                          or ack_question(sentence, self.asked))
                else self._news()) and not self.keep():
            self.edits.append((original, ""))           # the closing offer is not said, nor kept as said
            return
        if self._on_call(sentence):
            # the whole reply was "I'm here to help with whatever you need, sir." (sandbox 2026-11-21, his "Make it happen."): a
            # short acknowledgement in the character's voice instead of a help desk (core/character.py)
            from core import character
            self._say(original, character.line("ok", self.title))
            return
        if not self._over_budget(original, sentence):
            self._say(original, sentence)


def exchanges(history) -> list:
    """(what he said, what Jarvis answered) for each exchange of a conversation (role/content dicts), oldest first: his
    words without the notes Jarvis attaches (intelligence.last_user_text), Jarvis's last words in that exchange."""
    from core import intelligence
    out: list[list] = []
    for m in history or []:
        content = m.get("content")
        if m.get("role") == "user":
            said = intelligence.last_user_text([m])
            if said.strip():
                out.append([said, ""])
        if m.get("role") == "assistant" and out:
            text = content if isinstance(content, str) else " ".join(
                str(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") or "") for b in content or []
                if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) == "text")
            if text.strip():
                out[-1][1] = text.strip()
    return [(q, r) for q, r in out]


def polish(text: str, history=(), *, owner: bool = True, title: str = "sir", asked: str = "", keep=lambda: False,
           fresh=lambda: False) -> str:
    """A whole reply as JARVIS says it (see Polisher), against the conversation so far (`history`: role/content dicts)."""
    previous = ""
    for m in reversed(list(history or [])):
        if m.get("role") == "assistant":
            c = m.get("content")
            previous = c if isinstance(c, str) else " ".join(
                str(b.get("text", "")) for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
            break
    said: list[str] = []
    p = Polisher(said.append, previous, owner=owner, title=title, asked=asked, keep=keep, fresh=fresh,
                 earlier=exchanges(history))
    for sentence in _SPLIT.split((text or "").strip()):
        p(sentence)
    p.finish()
    return " ".join(s for s in said if s.strip())
