"""Daily self-review: once a day Jarvis reads his own previous day in data/jarvis.log and owns his misses. No model.

What counts as a miss (each found in code, from the log's YOU: / JARVIS: lines and the files that record what ran):
  order        he ordered one of his protocols (core.followthrough.order_in), the reply did nothing
               (followthrough.did_nothing: "Got it, sir.", or cut before a word) and no run of it began soon after
               (protocol-runs.json). Live 2026-11-20 23:48: "And Streaming Protocol." -> "Got it, sir." twice, nothing ran.
  commitment   a commitment (core.commitments) that resolved as NOT done that day (said already by the morning report's
               "Not done:", so only the voice answer and the Partner page repeat it)
  frustration  a sentence of his that is wholly frustration aimed at Jarvis (followthrough's matcher, "you're not
               understanding me", or an insult said outside a game); one right after a missed order belongs to that miss
  repeat       he said the same request again within REPEAT_S ("And the stream." x3), or Jarvis gave the same answer again
  cut_in       Jarvis was talking when he spoke ("(cut-in: N words, voice match X, ...)"), his own voice (match >= CUT_MATCH),
               not Jarvis's echo heard back ("YOU: Yes, sir.")

Days: the log has no time on its lines, only "===== Jarvis started YYYY-MM-DD HH:MM:SS =====" at each start. A voice line is
timed from its turn-timing row (data/learning/turn-timing-pc.jsonl: same words, aligned in order inside its session); any
other line lies between the timed lines around it (or the session's start and the next start / the log's last write). A line
whose earliest and latest possible moments fall on different days is uncertain: skipped and counted, never attributed.
Test traffic is never his: a turn whose timing row is marked "test" (core.turntrace.test_traffic) and "[Automatic" turns are
left out. Phone turns are not in this log (only voice turns print YOU:), so the review covers the PC's voice only.

Stored per day, one writer per machine file (as core.commitments): data/users/<owner>/selfreview-<machine>.json
{"v": 1, "days": {"YYYY-MM-DD": record}}. Only the PC builds (it holds the log); the laptop reads the PC's synced copy.
A record is said once: the morning report (core.commitments.build_report) carries its line and marks it only when said.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta

from core import storage

LOG = "jarvis.log"
TIMING = "learning/turn-timing-pc.jsonl"
FILE = "selfreview-{}.json"
MACHINES = ("pc", "server")
REPEAT_S = 120.0            # the same request again within this: he had to repeat himself
EPISODE_S = 5 * 60          # misses of one kind this close together are one episode
FOLLOW_S = 60.0             # a run that began this soon after the order answered it
SLACK_S = 5.0               # a timing row's start may trail the printed line by a moment
CUT_MATCH = 0.30            # a cut-in voice this close to his voiceprint is his (below: maybe the TV or the room)
CUT_MIN = 2                 # one cut-in is ordinary turn-taking; two or more are worth owning
SAY_WORDS = 25
SAY_ITEMS = 2
BUILD_AFTER_H = 3           # yesterday is reviewed from 03:00 on (a session running past midnight has settled by then)
MAX_DAYS = 14
RETRY_S = 600.0             # no log / unreadable: not tried again for this long
KEEP = 120

_lock = threading.RLock()
_tried: dict = {}

_HEADER = re.compile(r"^===== Jarvis started (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) =====\s*$")
_CUT = re.compile(r"^\s+\(cut-in: (\d+) words, voice match (-?[0-9.]+), (between sentences|while he talked)\)\s*$")
_GAME_ON = re.compile(r"^\[gaming\] .* started: game mode on")
_GAME_OFF = re.compile(r"^\[gaming\] .*(?:closed after|game mode off)")
_SUFFIX = re.compile(r"\s{2}\((?:status check; task continues|-> [^)]*)\)\s*$")
_WAKE = re.compile(r"^\W*(?:(?:hey|hi|okay|ok|yo)[,.\s]+)?jarvis[,.!\s]+", re.I)
# Insults aimed at Jarvis ("You are fucking annoying, bro.", "You can shut the fuck up."): a whole sentence, never during a game
# (he talks to his squad then).
_INSULT = re.compile(r"^\W*(?:fuck (?:you|off)|(?:you can )?shut (?:the fuck )?up|you suck|"
                     r"you(?:'re| are) (?:so |really |fucking |freaking |being )*(?:annoying|useless|stupid|dumb|trash|garbage|no help)|"
                     r"you(?:'re| are) not here for shit|you(?:'re| are) not (?:helping|doing anything))"
                     r"(?:[,\s]+(?:jarvis|bro|man|dude|sir))?\W*$", re.I)
_PLAIN_ORDER = re.compile(r"^\W*do it(?: (?:now|then))?(?:[,\s]+(?:jarvis|sir|bro|man|dude))?\W*$", re.I)
_CANCEL = re.compile(r"^\W*(?:no|nope|stop|cancel(?: that| it)?|never ?mind|forget (?:it|that)|wait|don'?t)\W*$", re.I)
_FILLER = {"yes", "yeah", "yep", "no", "nope", "okay", "ok", "thank you", "thanks", "thank you jarvis", "hey jarvis", "jarvis",
           "never mind", "all right", "alright", "go ahead", "yes sir", "yeah yeah", "okay okay", "no no", "non non", "stop it",
           "good night", "good morning", "let's see", "lets see", "hello", "hi"}
_AS_SAID = re.compile(r"^\W*as i said[:,]\s*", re.I)


# -- reading the log --------------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(text or "").casefold().replace("’", "'")))


def _key80(text: str) -> str:
    return _norm(str(text or "")[:80])


def _lcs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Index pairs of a longest common subsequence (a turn without a timing row, a row without a printed line, are skipped)."""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def parse(lines, timing: list[dict] | None = None, end: float | None = None, around: tuple | None = None) -> dict:
    """The log's turns and cut-ins, each with the earliest and latest moment it can have happened ("lo"/"hi", epoch s) and its
    day when both fall on the same date (else None: uncertain). `timing`: turn-timing rows (voice); `end`: the log's last
    write (bounds the last session); `around`: (start, stop) epoch: only sessions overlapping it are read in detail."""
    sessions, cur = [], None
    for raw in lines:
        line = raw.rstrip("\r\n")
        m = _HEADER.match(line)
        if m:
            try:
                start = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                cur = None
                continue
            cur = {"start": start, "lines": []}
            sessions.append(cur)
            continue
        if cur is not None:
            cur["lines"].append(line)
    for k, s in enumerate(sessions):
        s["end"] = sessions[k + 1]["start"] if k + 1 < len(sessions) else end
    voice = sorted((r for r in (timing or []) if r.get("channel") == "voice" and isinstance(r.get("at"), (int, float))),
                   key=lambda r: r["at"])
    turns, cuts, gaming = [], [], False
    for s in sessions:
        stop = s["end"] if s["end"] is not None else float("inf")
        if around and (stop < around[0] or s["start"] > around[1]):
            gaming = _ends_gaming(s["lines"], gaming)
            continue
        gaming = _session(s, voice, turns, cuts, gaming)
    return {"turns": turns, "cuts": cuts}


def _ends_gaming(lines: list[str], gaming: bool) -> bool:
    for line in lines:
        if _GAME_ON.match(line):
            gaming = True
        elif _GAME_OFF.match(line):
            gaming = False
    return gaming


def _session(s: dict, voice: list[dict], turns: list, cuts: list, gaming: bool = False) -> bool:
    """One session's turns and cut-ins (appended); -> whether a game was still on at its end (a restart mid-game keeps it on
    until the game is seen closing)."""
    events, last_turn = [], None
    for line in s["lines"]:
        if line.startswith("YOU: "):
            text = _SUFFIX.sub("", line[5:]).strip()
            last_turn = {"kind": "turn", "text": text, "reply": [], "gaming": gaming, "cut": False, "ts": None, "test": False}
            events.append(last_turn)
        elif line.startswith("JARVIS: "):
            if last_turn is not None:
                last_turn["reply"].append(line[8:].strip())
        elif line.strip() == "(interrupted)":
            if last_turn is not None and not last_turn["reply"]:
                last_turn["cut"] = True
        else:
            m = _CUT.match(line)
            if m:
                events.append({"kind": "cut", "words": int(m.group(1)), "match": float(m.group(2)), "how": m.group(3)})
            elif _GAME_ON.match(line):
                gaming = True
            elif _GAME_OFF.match(line):
                gaming = False
    stop = s["end"] if s["end"] is not None else float("inf")
    rows = [r for r in voice if s["start"] - SLACK_S <= r["at"] <= stop + SLACK_S]
    said = [e for e in events if e["kind"] == "turn"]
    keys, row_keys = [_key80(e["text"]) for e in said], [_key80(r.get("said", "")) for r in rows]
    # The same words said more often than they have rows (an interrupted turn writes none): which line a row belongs to is
    # unknown, so none of them is timed (review 2026-11-21: "Start the streaming protocol." x2 with one row put the first
    # one's time a day late). Their bounds then come from the lines around them.
    counts, row_counts = {}, {}
    for k in keys:
        counts[k] = counts.get(k, 0) + 1
    for k in row_keys:
        row_counts[k] = row_counts.get(k, 0) + 1
    for i, j in _lcs(keys, row_keys):
        if counts[keys[i]] != row_counts.get(keys[i], 0):
            continue
        said[i]["ts"] = float(rows[j]["at"])
        said[i]["test"] = bool(rows[j].get("test"))
    if any(counts[k] != row_counts.get(k, 0) and row_counts.get(k) for k in counts):
        for e in said:                    # a test row among the ambiguous ones still marks those words as test traffic
            if row_counts.get(_key80(e["text"])) and any(r.get("test") for r in rows if _key80(r.get("said", "")) == _key80(e["text"])):
                e["test"] = True
    lo = s["start"]
    for e in events:                      # earliest: the last timed turn at or before it
        if e.get("ts") is not None:
            lo = max(lo, e["ts"])
        e["lo"] = e["ts"] if e.get("ts") is not None else lo
    hi = s["end"]
    for e in reversed(events):            # latest: the next timed turn after it, else the session's end
        if e.get("ts") is not None:
            e["hi"] = e["ts"]
            hi = e["ts"] if hi is None else min(hi, e["ts"])
        else:
            e["hi"] = hi
    for k, e in enumerate(events):
        e["day"] = _day_of(e["lo"], e["hi"])
        if e["kind"] == "cut":
            nxt = next((n for n in events[k + 1:] if n["kind"] == "turn"), None)
            e["echo"] = nxt is not None and _is_echo(nxt["text"])
            e["test"] = bool(nxt and nxt.get("test"))
            cuts.append(e)
        else:
            e["echo"] = _is_echo(e["text"])
            e["automatic"] = bool(re.match(r"\s*\[Automatic", e["text"], re.I))
            turns.append(e)
    for e in turns:
        e.setdefault("session", s["start"])
    return gaming


def _day_of(lo: float | None, hi: float | None) -> str | None:
    if lo is None or hi is None or hi < lo:
        return None
    a, b = datetime.fromtimestamp(lo).date(), datetime.fromtimestamp(hi).date()
    return a.isoformat() if a == b else None


def _is_echo(text: str) -> bool:
    from core import followthrough
    return bool(followthrough._ECHO.match(text or ""))


# -- finding the misses -----------------------------------------------------------------------------------------------
def _sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?;])\s+", (text or "").replace("’", "'").strip()) if p.strip()]


def frustrated_line(text: str, gaming: bool = False) -> bool:
    """One of his sentences is wholly frustration aimed at Jarvis (followthrough's matcher, after 'Hey Jarvis'), or an
    insult said outside a game."""
    from core import followthrough
    for part in _sentences(text):
        part = _WAKE.sub("", part).strip()
        if _PLAIN_ORDER.match(part):
            continue                      # "Do it." is an instruction (followthrough reads it as anger only right after a miss)
        if followthrough._FRUSTRATION.match(part) or (not gaming and _INSULT.match(part)):
            return True
    return False


def _content(text: str) -> str:
    return _norm(_WAKE.sub("", (text or "").replace("’", "'")))


def _same(a: str, b: str, ratio: float = 0.8) -> bool:
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= ratio


def _runs(uid: str | None = None) -> tuple[list[tuple[str, float]], float]:
    """(name, started) of every protocol run kept, and the moment from which that list is complete (it keeps MAX_RUNS).
    `uid`: that person's runs (core.scorecard reads the owner's whoever asks); None: the current person's."""
    from core import protocols
    rows = storage.load(protocols.RUNS, []) if uid is None else storage._read(storage.user_dir(uid) / protocols.RUNS, [])
    rows = rows if isinstance(rows, list) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            out.append((str(r.get("name") or ""), datetime.fromisoformat(str(r.get("started"))).timestamp()))
        except (TypeError, ValueError):
            continue
    known = min((t for _, t in out), default=0.0) if len(rows) >= protocols.MAX_RUNS else 0.0
    return out, known


def review(day: str, parsed: dict, runs: list[tuple[str, float]], runs_known: float = 0.0, failed: list[dict] | None = None) -> dict:
    """The day's misses from the parsed log (pure: no files). -> the record (without storage fields)."""
    from core import followthrough
    turns = [t for t in parsed["turns"] if not t["echo"] and not t["test"] and not t["automatic"]]
    mine = [t for t in turns if t["day"] == day]
    uncertain = sum(1 for t in turns if t["day"] is None and _could_be(t, day))
    misses, skipped_orders = [], 0
    for k, t in enumerate(mine):
        proto = followthrough.order_in(t["text"])
        if proto is None or not followthrough.did_nothing(" ".join(t["reply"])):
            continue
        nxt = next((n for n in mine[k + 1:] if n["session"] == t["session"]), None)
        if nxt is not None and _CANCEL.match(nxt["text"]) and nxt["hi"] - t["lo"] <= REPEAT_S:
            continue                                    # "no, wait": he called it off
        if t["lo"] - SLACK_S < runs_known:
            skipped_orders += 1                         # the run history no longer reaches back this far: unknown
            continue
        name = proto["name"]
        if any(n == name and t["lo"] - SLACK_S <= at <= t["hi"] + FOLLOW_S for n, at in runs):
            continue
        prev = misses[-1] if misses and misses[-1]["name"] == name else None
        if prev is not None and t["lo"] - prev["last"] <= EPISODE_S:
            prev.update(count=prev["count"] + 1, last=t["hi"], turns=prev["turns"] + [id(t)])
            if prev["exact"] is False and t["ts"] is not None:
                prev.update(at=t["ts"], exact=True)
            continue
        misses.append({"kind": "order", "name": name, "at": t["ts"] if t["ts"] is not None else t["lo"],
                       "exact": t["ts"] is not None, "count": 1, "first": t["lo"], "last": t["hi"], "said": t["text"][:KEEP],
                       "turns": [id(t)], "frustrated": False})
    in_miss = {i for m in misses for i in m["turns"]}
    angry, folded = [], set()
    for t in mine:
        if not frustrated_line(t["text"], t["gaming"]):
            continue
        folded.add(id(t))
        owner = next((m for m in misses if m["first"] - SLACK_S <= t["lo"] <= m["last"] + EPISODE_S), None)
        if owner is not None:
            owner["frustrated"] = True
            continue
        if angry and t["lo"] - angry[-1]["last"] <= EPISODE_S:
            angry[-1].update(lines=angry[-1]["lines"] + 1, last=t["hi"])
            continue
        angry.append({"at": t["ts"] if t["ts"] is not None else t["lo"], "exact": t["ts"] is not None, "lines": 1,
                      "last": t["hi"], "said": t["text"][:KEEP]})
    # he said it again
    linked: dict = {}
    for k, t in enumerate(mine):
        words = _content(t["text"])
        if (id(t) in in_miss or id(t) in folded or len(words.split()) < 2 or words in _FILLER):
            continue
        for p in reversed(mine[max(0, k - 3):k]):
            if p["session"] != t["session"] or t["hi"] - p["lo"] > REPEAT_S:
                break
            if id(p) not in in_miss and _same(_content(p["text"]), words) and _failed_try(p, t):
                linked[id(t)] = linked.get(id(p), id(p))
                break
    episodes: dict = {}
    by_id = {id(t): t for t in mine}
    for child, root in linked.items():
        episodes.setdefault(root, [root]).append(child)
    repeats = [{"at": by_id[r]["ts"] if by_id[r]["ts"] is not None else by_id[r]["lo"], "exact": by_id[r]["ts"] is not None,
                "times": len(ids), "said": by_id[r]["text"][:KEEP]} for r, ids in episodes.items()]
    repeats.sort(key=lambda r: r["at"])
    # Jarvis gave him the same answer again, and said so ("As I said: You're live on Twitch..."): never a guess from two
    # similar replies ("Timer set for five minutes" / "... ten minutes" are two answers; review 2026-11-21)
    # ... and that counts when he said the same thing again too: on 11-20 his "And stream." (he meant END) got "As I said:
    # You're live on Twitch" five times (review round 2 asked to skip those; the real night says they are the miss)
    again = [{"at": t["ts"] if t["ts"] is not None else t["lo"], "said": t["reply"][0][:KEEP]}
             for t in mine if t["reply"] and _AS_SAID.match(t["reply"][0])]
    cuts = [c for c in parsed["cuts"] if c["day"] == day and not c["echo"] and not c["test"] and c["match"] >= CUT_MATCH]
    items = [{k: v for k, v in m.items() if k not in ("turns", "first", "last")} for m in misses]
    for c in failed or []:
        items.append({"kind": "commitment", "what": str(c.get("what", ""))[:KEEP], "detail": str(c.get("detail", ""))[:KEEP],
                      "at": float(c.get("resolved") or 0), "exact": True})
    if angry:
        items.append({"kind": "frustration", "count": len(angry), "at": angry[0]["at"], "exact": angry[0]["exact"],
                      "said": angry[0]["said"], "lines": sum(a["lines"] for a in angry)})
    if repeats:
        items.append({"kind": "repeat", "count": len(repeats), "at": repeats[0]["at"], "exact": repeats[0]["exact"],
                      "said": [r["said"] for r in repeats[:4]], "times": sum(r["times"] for r in repeats)})
    if again:
        items.append({"kind": "my_repeat", "count": len(again), "at": again[0]["at"], "exact": True,
                      "said": again[0]["said"]})
    if len(cuts) >= CUT_MIN:
        items.append({"kind": "cut_in", "count": len(cuts), "at": cuts[0]["lo"], "exact": False})
    return {"day": day, "turns": len(mine), "uncertain": uncertain, "orders_unknown": skipped_orders, "items": items,
            "counts": {"orders": sum(m["count"] for m in misses), "frustration": sum(a["lines"] for a in angry),
                       "repeats": len(repeats), "my_repeats": len(again), "cut_ins": len(cuts),
                       "commitments": len(failed or [])}}


_NOT_UNDERSTOOD = re.compile(r"couldn't work out|not sure what you meant|lost my train of thought|i didn't catch|"
                             r"what do you (?:need|want) me to|did you mean|i don't know that word", re.I)


def _failed_try(t: dict, again: dict | None = None) -> bool:
    """His first try got nothing: cut before a word, 'I couldn't work out ...', or a bare acknowledgment that his second
    try showed was empty ("Got it, sir." then "Opening the garage camera.").
    Asking again after a real answer ("What time is it?" twice, "Turn the volume up" twice) is not Jarvis's miss, and a bare
    "Okay." answered by another bare "Okay." is how a command that worked twice sounds (review round 2, 2026-11-21:
    did_nothing() alone flagged volume and music commands said twice)."""
    from core import followthrough
    reply = " ".join(t.get("reply") or [])
    if (t.get("cut") and not t.get("reply")) or _NOT_UNDERSTOOD.search(reply):
        return True
    second = " ".join((again or {}).get("reply") or [])
    return bool(reply and second and followthrough.did_nothing(reply) and not followthrough.did_nothing(second))


def _could_be(t: dict, day: str) -> bool:
    lo = datetime.fromtimestamp(t["lo"]).date().isoformat() if t.get("lo") is not None else None
    hi = datetime.fromtimestamp(t["hi"]).date().isoformat() if t.get("hi") is not None else None
    return (lo is None or lo <= day) and (hi is None or day <= hi)


# -- what he hears ----------------------------------------------------------------------------------------------------
def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def phrase(item: dict, when: bool = True) -> str:
    kind = item.get("kind")
    at = (" at " if item.get("exact") else " around ") + clock(item["at"]) if when and item.get("at") else ""
    if kind == "order":
        return f"the {item['name']} protocol didn't start when you asked{at}"
    if kind == "commitment":
        return f"I didn't get this done: {item['what']}"
    if kind == "frustration":
        return "you got frustrated with me" + (at if item["count"] == 1 else " " + _times(item["count"]))
    if kind == "repeat":
        return "you had to repeat yourself " + _times(item["count"])
    if kind == "my_repeat":
        return "I repeated myself " + _times(item["count"])
    if kind == "cut_in":
        return "I talked over you " + _times(item["count"])
    return ""


def spoken(rec: dict | None, commitments: bool = False) -> str:
    """The morning line: the two most important misses, at most SAY_WORDS words; '' for a clean day."""
    if not rec:
        return ""
    items = [i for i in rec.get("items", []) if commitments or i.get("kind") != "commitment"]
    if not items:
        return ""
    n = len(items)
    lead = {1: "Yesterday I let you down once: ", 2: "Yesterday I let you down twice: "}.get(n, f"Yesterday I got {n} things wrong. Worst: ")
    tries = []
    first, second = items[0], items[1] if n > 1 else None
    if second is not None:
        tries += [[phrase(first), phrase(second)], [phrase(first), phrase(second, False)], [phrase(first, False), phrase(second, False)]]
    tries += [[phrase(first)], [phrase(first, False)]]
    for parts in tries:
        if len(parts) == 1 and second is not None:
            lead = "Yesterday I let you down. Worst: "
        line = lead + (parts[0] + (", and " + parts[1] if len(parts) > 1 else "")) + "."
        if len(line.split()) <= SAY_WORDS:
            return line
    return line


def details(rec: dict | None) -> str:
    """The voice answer: what he asked for, every kind of miss, with times."""
    if not rec:
        return ""
    items = rec.get("items", [])
    out = []
    for it in items[:5]:
        text = phrase(it)
        if it.get("kind") == "order" and it.get("count", 1) > 1:
            text += f" (you asked {_times(it['count'])})"
        if it.get("kind") == "repeat" and it.get("said"):
            text += f", for example '{_short(it['said'][0])}'"
        out.append(text)
    head = f"Yesterday {_times(len(items)).replace('once', 'one thing').replace('twice', 'two things').replace(' times', ' things')} went wrong on my side: " \
        if items else ""
    if not items and not rec.get("turns"):
        body = "I have nothing from yesterday to review: you didn't talk to me at the PC, or I couldn't place those lines on the day."
    elif not items:
        body = "I found nothing I got wrong yesterday in what you said to me at the PC."
    else:
        body = head + "; ".join(out) + "."
        body = body[0].upper() + body[1:]
    extra = []
    if rec.get("uncertain"):
        extra.append(f"I couldn't tell which day {_n(rec['uncertain'], 'line')} belonged to, so I left them out")
    if rec.get("orders_unknown"):
        extra.append(f"I couldn't check {_n(rec['orders_unknown'], 'order')} against the protocol history")
    return body + (" " + ". ".join(e[0].upper() + e[1:] for e in extra) + "." if extra else "")


def _n(n: int, word: str) -> str:
    from core.words import plural
    return plural(n, word)


def _short(text: str, words: int = 6) -> str:
    w = _WAKE.sub("", str(text or "")).strip(" .!?,").split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


# -- storage ----------------------------------------------------------------------------------------------------------
def _machine() -> str:
    from core import commitments
    return commitments._machine()


def _owner() -> bool:
    from core import users
    try:
        return users.get(users.current()).get("role") == "owner"
    except Exception:  # noqa: BLE001 - unknown: not the owner (nothing is said)
        return False


def _load(machine: str) -> dict:
    data = storage.load(FILE.format(machine), {})
    days = data.get("days") if isinstance(data, dict) else None
    if not isinstance(days, dict):
        return {}
    return {d: r for d, r in days.items() if isinstance(d, str) and isinstance(r, dict) and isinstance(r.get("items", []), list)}


def record_for(day: str) -> dict | None:
    """The day's record, from either machine's file (newest copy wins). Read-only."""
    if not _owner():
        return None
    best = None
    for m in MACHINES:
        rec = _load(m).get(day)
        try:
            upd = float((rec or {}).get("updated") or 0)
        except (TypeError, ValueError):
            continue
        if rec is not None and (best is None or upd > float(best.get("updated") or 0)):
            best = rec
    return best


def _put(rec: dict) -> dict:
    with _lock:
        days = _load(_machine())
        newest = float((record_for(rec["day"]) or {}).get("updated") or 0)
        rec = {**rec, "updated": max(time.time(), newest + 0.001)}
        days[rec["day"]] = rec
        for d in sorted(days)[:-MAX_DAYS]:
            days.pop(d, None)
        storage.save(FILE.format(_machine()), {"v": 1, "days": days})
        return rec


def _log_path():
    return storage.DATA / LOG


def _read_timing() -> list[dict]:
    rows = []
    try:
        with open(storage.DATA / TIMING, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _failed(day: str) -> list[dict]:
    from core import commitments
    out = []
    for c in commitments.items(("failed", "reported")):
        if (c.get("outcome") or c.get("state")) != "failed":
            continue
        try:
            if datetime.fromtimestamp(float(c.get("resolved") or 0)).date().isoformat() == day:
                out.append(c)
        except (TypeError, ValueError, OSError):
            continue
    return out


def build(day: str, now: datetime | None = None) -> dict | None:
    """Read the log (read-only) and store the day's record. None when it cannot be built here (not the PC, no log)."""
    if not _owner() or _machine() != "pc":
        return None
    path = _log_path()
    try:
        end = os.path.getmtime(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    start = datetime.fromisoformat(day).timestamp()
    parsed = parse(lines, _read_timing(), end=end, around=(start - 86400, start + 2 * 86400))
    runs, known = _runs()
    rec = review(day, parsed, runs, known, _failed(day))
    rec.update(v=1, built=(now or _now()).timestamp(), machine=_machine(), said=None)
    return _put(rec)


def ensure(day: str, now: datetime | None = None) -> dict | None:
    """The stored record, building it on the PC once the day is over (from BUILD_AFTER_H the next morning)."""
    rec = record_for(day)
    if rec is not None or not _owner():
        return rec
    now = now or _now()
    ready = datetime.fromisoformat(day) + timedelta(days=1, hours=BUILD_AFTER_H)
    if now < ready or _machine() != "pc":
        return None
    with _lock:
        if time.monotonic() - _tried.get(day, -1e9) < RETRY_S:
            return None
        _tried[day] = time.monotonic()
    try:
        return build(day, now)
    except Exception as error:  # noqa: BLE001 - a review never breaks the report
        print(f"[selfreview] {day}: {type(error).__name__}: {error}")
        return None


def _now() -> datetime:
    return datetime.now()


def yesterday(now: datetime | None = None) -> str:
    return ((now or _now()).date() - timedelta(days=1)).isoformat()


def for_report(now: datetime | None = None) -> tuple[str, str | None]:
    """(the morning line, its day) when yesterday's review has something not yet said; else ('', None). Failed commitments
    are left out: the report's 'Not done:' says them."""
    now = now or _now()
    day = yesterday(now)
    rec = ensure(day, now)
    if not rec or rec.get("said"):
        return "", None
    line = spoken(rec)
    return (line, day) if line else ("", None)


def mark_said(day: str, at: float | None = None) -> None:
    rec = record_for(day)
    if rec is None or rec.get("said"):
        return
    _put({**rec, "said": float(at if at is not None else time.time())})


def page_row(now: datetime | None = None) -> dict | None:
    """Yesterday's review for the Partner page (None: not reviewed, or not the owner)."""
    now = now or _now()
    day = yesterday(now)
    rec = ensure(day, now)
    if rec is None:
        return None
    rows = []
    for it in rec.get("items", []):
        text = phrase(it)
        if it.get("kind") == "order" and it.get("count", 1) > 1:
            text += f" (you asked {_times(it['count'])})"
        rows.append({"kind": it.get("kind"), "text": text[:1].upper() + text[1:] + ".",
                     "said": it.get("said") if isinstance(it.get("said"), str) else ""})
    return {"day": day, "line": spoken(rec, commitments=True), "items": rows, "clean": not rows, "turns": rec.get("turns", 0),
            "uncertain": rec.get("uncertain", 0), "said": rec.get("said"), "counts": rec.get("counts", {})}


# -- "what did you get wrong yesterday" -------------------------------------------------------------------------------
_DAYWORD = r"[\s,]+(?:yesterday|last night)"
_ASK = re.compile(
    r"^\W*(?:(?:hey\s+)?jarvis[\s,]+)?(?:(?:ok(?:ay)?|so|and|well|um+|uh+|alright|all right)[\s,]+)*(?:please\s+)?"
    r"(?:(?:so\s+)?(?:what|which things?) (?:did|have) you (?:get|got|do|mess|screw) (?:wrong|up)" + _DAYWORD +
    r"|what (?:mistakes|misses) did you make" + _DAYWORD +
    r"|(?:did|have) you (?:get anything wrong|mess (?:anything|something) up|screw (?:anything|something) up|let me down)" + _DAYWORD +
    r"|where did you (?:let me down|go wrong|mess up)" + _DAYWORD +
    r"|what did you (?:miss|fail to do)" + _DAYWORD +
    r"|how did you do(?: for me)?[\s,]+(?:yesterday|last night)"
    r"|(?:tell me |give me )?(?:your|the) self[- ]review)"
    r"(?:[\s,]+(?:please|jarvis|sir|for me))*\W*$", re.I)


def asks(text: str) -> bool:
    return bool(_ASK.match((text or "").replace("’", "'").strip()))


def handle(text: str, now: datetime | None = None) -> str | None:
    """His question about yesterday's misses -> the answer from the stored record; None for any other line."""
    if not asks(text):
        return None
    if not _owner():
        return "I only review the owner's conversations at the PC."
    now = now or _now()
    day = yesterday(now)
    rec = ensure(day, now)
    if rec is None:
        if _machine() != "pc":
            return "I review yesterday from the PC's log, and its review hasn't reached me here yet."
        ready = datetime.fromisoformat(day) + timedelta(days=1, hours=BUILD_AFTER_H)
        return ("I review yesterday from 3 in the morning, once the day is over." if now < ready
                else "I couldn't read yesterday's log, so I can't tell you yet.")
    answer = details(rec)
    if rec.get("items"):
        mark_said(day, now.timestamp())
    return answer
