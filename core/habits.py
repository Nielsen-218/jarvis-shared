"""Habit learning: Jarvis notices WHEN the owner does things and PROPOSES a protocol; it never acts on one he hasn't approved.

Why this and not more skill-lab work: the skill lab learned one skill in about nine days. Learning WHEN he does things is
the dependable kind of self-improvement, and it ends in something he controls (core/protocols: data, approval, undo).

Where the evidence comes from (all local files, nothing leaves the machine, no model):
  game      data/users/<id>/gaming/sessions.json (tools/gaming: every game session, its start time)
  app       data/users/<id>/app-starts-pc.jsonl (observe() below: an app's window appeared, process name only), and his
            spoken "open X" lines in data/learning/turn-timing-pc.jsonl (channel voice only: the phone channel is mostly
            replay and live-check traffic, 1,393 rows of test lines on 2026-11-16)

The bar (measured against his real logs on 2026-11-16, see DEVELOPMENT-HANDOFF): a habit is a thing he did on at least
MIN_HITS different days within WINDOW_MIN minutes of one time, on at least MIN_SHARE of the days Jarvis was watching in
the last LOOKBACK_DAYS, at least MIN_RECENT times in the last RECENT_DAYS. "Watching" counts every day any source has a row,
which is generous to the denominator on purpose: a day with no data is not evidence, a day he didn't play is.
A daily time trigger that fires on days he doesn't play opens his game for nothing, so a weak pattern is reported as
a near miss with its numbers, never proposed.

Flow: the scheduler (PC) calls tick() at most once a day; a new habit becomes an UNAPPROVED protocol (marked "learned")
and one sentence through core.attention asks him. Only "approve the <name> protocol" (core/protocols' own approval,
bound to the exact steps and trigger) lets it run. The first time it runs on its own he is told once, with how to undo
it; later runs speak only when a step failed. "Stop doing that" (after it ran or was suggested) deletes it and the habit
is never suggested again; so does deleting the protocol by name."""
from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from core import storage
from core.words import plural

LOOKBACK_DAYS = 28
WINDOW_MIN = 45            # "around 8": within 45 minutes of the habit's time
MIN_HITS = 4               # different days, not events (he opened ChatGPT 3x in one evening: one day)
MIN_SHARE = 0.6            # of the days Jarvis was watching (in the trigger's day set)
MIN_WATCHED = 7            # fewer watched days than this: too thin to say "usually"
RECENT_DAYS, MIN_RECENT = 14, 2
STOP_WINDOW_S = 30 * 60    # "stop doing that" refers to a learned protocol suggested or run this recently, nothing between
PROPOSE_HOURS = (8, 21)    # suggestions are made between 8 AM and 9 PM (core.attention still holds them while he's away)
PENDING_DAYS = 3           # one open suggestion at a time; another only after he answered or this many days passed
STATE = "habits.json"
APP_LOG_MAX = 6000

GAMES_FILE = "gaming/sessions.json"
# Windows that come and go by themselves, or are Jarvis: never "an app he opened".
_NOT_APPS = {"python", "pythonw", "py", "explorer", "applicationframehost", "textinputhost", "searchhost", "searchapp",
             "shellexperiencehost", "startmenuexperiencehost", "lockapp", "systemsettings", "msedgewebview2",
             "steamwebhelper", "nvidia overlay", "nvcontainer", "dwm", "taskmgr", "cmd", "conhost", "powershell", "pwsh",
             "windowsterminal", "openconsole", "rundll32", "dllhost", "sihost", "ctfmon", "smartscreen", "consent",
             "securityhealthsystray", "widgets", "phoneexperiencehost", "gamebar", "gamebarftserver", "crossdeviceresume",
             "ollama", "ollama app", "llama-server", "jarvis", "companion", "notepad", "calc", "calculatorapp", "mspaint",
             "werfault", "setup", "installer", "msiexec"}
# notepad / calculator / paint are what Jarvis's live checks and the owner's demos open (184 Notepad opens in the PC
# ledger on 10 days, every one a test of Jarvis): their opens say nothing about his day.

_lock = threading.Lock()
_prev_apps: set | None = None
_last_heartbeat = ""
_first_look = 0.0
_logged_day, _logged_today = "", set()
SETTLE_S = 5 * 60          # apps that appear in Jarvis's first minutes started with Windows (Discord at login), not by him


# -- reading the evidence --------------------------------------------------------------------------------------------
def _app_log_path():
    """Only the PC watches its windows and writes this (synced folder: one writer); the laptop reads the same file."""
    return storage.path("app-starts-pc.jsonl")


def _jsonl(path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _spoken_open(said: str) -> str:
    """'Open ChatGPT.' -> 'chatgpt'; anything that is not exactly one open-an-app step -> ''."""
    from core import protocols
    text = re.sub(r"^\[[^\]]*\]\s*", "", said or "").strip()
    text = re.sub(r"^\W*(?:(?:hey|ok|okay)\s+)?(?:jarvis\W+)?", "", text, flags=re.I)
    text = re.sub(r"^(?:can you|could you|please|i need you to|i want you to)\s+", "", text, flags=re.I).strip(" .!?")
    if not re.match(r"^(?:open|launch|start|fire up|bring up|load)\b", text, re.I) or len(text) > 50:
        return ""
    # one sentence, never taken back: "Open Discord. Never mind." / "Open notepad. Stop." opened nothing he wanted
    if re.search(r"[.!?;]\s*\S", text) or _CANCEL.search(text):
        return ""
    try:
        step = protocols._step(text)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(step, dict) and step.get("do") == "open_app":
        target = step["target"]
        return "" if _key_app(target) in _NOT_APPS else target
    return ""


_CANCEL = re.compile(r"\b(?:never ?mind|cancel|stop|don'?t|not|no)\b", re.I)


def _key_app(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).lower().removesuffix(".exe")).strip()


def _when(value, iso: bool = False) -> datetime | None:
    """A row's time as local naive datetime; None for anything malformed (one bad row never stops the pass)."""
    try:
        if iso:
            t = datetime.fromisoformat(str(value))
            return t.astimezone().replace(tzinfo=None) if t.tzinfo else t
        return datetime.fromtimestamp(float(value))
    except Exception:  # noqa: BLE001 - None, garbage, Infinity, out of range
        return None


def evidence(now: datetime | None = None) -> tuple[list[dict], set]:
    """([{kind, subject, t}], the days Jarvis was watching) inside the lookback."""
    now = now or datetime.now()
    since = now - timedelta(days=LOOKBACK_DAYS)
    events: list[dict] = []
    watched: set = set()
    for s in storage.load(GAMES_FILE, []) or []:
        t = _when(s.get("start") if isinstance(s, dict) else None, iso=True)
        if t and since <= t <= now and s.get("game"):
            events.append({"kind": "game", "subject": str(s["game"]), "t": t})
            watched.add(t.date())
    timing = storage.DATA / "learning" / "turn-timing-pc.jsonl"
    for row in _jsonl(timing):
        t = _when(row.get("at"))
        if not t or not since <= t <= now:
            continue
        watched.add(t.date())                       # Jarvis was running on the PC that day
        if row.get("channel") == "voice":           # only his microphone: the phone channel is mostly test traffic
            app = _spoken_open(str(row.get("said") or ""))
            if app:
                events.append({"kind": "app", "subject": app, "t": t})
    for row in _jsonl(_app_log_path()):
        t = _when(row.get("t"))
        if not t or not since <= t <= now:
            continue
        watched.add(t.date())
        if row.get("app"):
            events.append({"kind": "app", "subject": str(row["app"]), "t": t})
    return events, watched


# -- finding habits --------------------------------------------------------------------------------------------------
DAYSETS = (("every day", None), ("on weekdays", (0, 1, 2, 3, 4)), ("on weekends", (5, 6)))


def _minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def _gap(a: int, b: int) -> int:
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def _best_center(times: list[int]) -> tuple[int, list[int]]:
    """The time with the most of these within WINDOW_MIN (each one tried as the center), then their median."""
    best: list[int] = []
    for c in times:
        near = [x for x in times if _gap(x, c) <= WINDOW_MIN]
        if len(near) > len(best):
            best = near
    if not best:
        return 0, []
    ordered = sorted(best)
    center = ordered[len(ordered) // 2]
    return center, [x for x in times if _gap(x, center) <= WINDOW_MIN]


def _quarter(minutes: int) -> str:
    q = int(round(minutes / 15.0)) * 15 % 1440
    return f"{q // 60:02d}:{q % 60:02d}"


def analyse(events: list[dict], watched: set, now: datetime | None = None) -> list[dict]:
    """Every subject seen on 2+ days, with its best time and day set and whether it clears the bar (`habit`) and, if
    not, why (`short`). Sorted habits first, then by how close it came."""
    now = now or datetime.now()
    recent_from = (now - timedelta(days=RECENT_DAYS)).date()
    by: dict = defaultdict(list)
    for e in events:
        key = (e["kind"], e["subject"] if e["kind"] == "game" else _key_app(e["subject"]))
        by[key].append(e)
    out = []
    for (kind, subject), rows in by.items():
        days = defaultdict(list)
        for e in rows:
            days[e["t"].date()].append(_minutes(e["t"]))
        if len(days) < 2:
            continue
        best = None
        for label, dayset in DAYSETS:
            in_set = {d: ts for d, ts in days.items() if dayset is None or d.weekday() in dayset}
            pool = [d for d in watched if dayset is None or d.weekday() in dayset]
            if not in_set or not pool:
                continue
            firsts = [min(ts) for ts in in_set.values()]
            center, _ = _best_center(firsts)
            hit_days = sorted(d for d, ts in in_set.items() if any(_gap(x, center) <= WINDOW_MIN for x in ts))
            share = len(hit_days) / len(pool)
            recent = sum(1 for d in hit_days if d >= recent_from)
            short = ""
            if len(pool) < MIN_WATCHED:
                short = f"I've only been watching on {plural(len(pool), 'day')}"
            elif len(hit_days) < MIN_HITS:
                short = f"only {plural(len(hit_days), 'day')} near one time"
            elif share < MIN_SHARE:
                short = f"{len(hit_days)} of {plural(len(pool), 'day')} is too few to call it usual"
            elif recent < MIN_RECENT:
                short = "not recently"
            cand = {"kind": kind, "subject": subject, "label": label, "days": list(dayset) if dayset else None,
                    "at": _quarter(center), "hits": len(hit_days), "watched": len(pool), "share": round(share, 2),
                    "event_days": len(in_set), "recent": recent, "habit": not short, "short": short,
                    "first_times": sorted(firsts)}
            if cand["habit"] and dayset is not None and _gap(center, 0) <= 60:
                # near midnight the evening's events and the trigger's weekday fall on different dates: every day only
                cand.update(habit=False, short="too close to midnight to tie to certain days")
            # Among habits the best fit wins (Mon-Fri 20 of 20 beats every day 20 of 28: it would open the app on weekends);
            # a narrower day set must clear the bar to be chosen, otherwise every day is what's reported.
            rank = (cand["habit"], share if cand["habit"] else 0.0, dayset is None, share, len(hit_days))
            if best is None or rank > best[0]:
                best = (rank, cand)
        if best:
            out.append(best[1])
    out.sort(key=lambda c: (c["habit"], c["share"], c["hits"]), reverse=True)
    return out


def key_of(c: dict) -> str:
    """What 'never suggest it again' remembers: the thing, not the days (declined every day, it may later look like
    weekdays only; it is still the thing he said no to)."""
    return f"{c['kind']}:{_key_app(c['subject'])}"


# -- turning a habit into a protocol proposal ------------------------------------------------------------------------
def _bucket(hhmm: str) -> str:
    h = int(hhmm[:2])
    return "morning" if 5 <= h < 12 else "afternoon" if h < 17 else "evening" if h < 22 else "night"


def _clock(hhmm: str) -> str:
    from core import protocols
    return protocols._clock(hhmm)


def plan(c: dict) -> dict | None:
    """The protocol a habit would become: for a game that one of his protocols already opens, that protocol's steps
    (the Gaming protocol at 8 PM); otherwise open that app. None when Jarvis couldn't do it (no such app here)."""
    from core import protocols
    subject = c["subject"]
    if c["kind"] == "game":
        for p in protocols.protocols():
            if p.get("learned") or not p.get("steps"):
                continue
            if any(s.get("do") == "open_app" and _names_game(s.get("target", ""), subject) for s in p["steps"]):
                base, steps = p["name"], [dict(s) for s in p["steps"]]
                break
        else:
            base, steps = _key_app(subject), [{"do": "open_app", "target": _key_app(subject)}]
    else:
        target = _key_app(subject)
        try:
            from core import pcbridge
            if pcbridge.local_pc() and not protocols.resolve(target):
                return None
        except Exception:  # noqa: BLE001 - the catalog could not be read: not proposed today
            return None
        base, steps = target, [{"do": "open_app", "target": target}]
    when = {"every day": "", "on weekdays": "weekday ", "on weekends": "weekend "}[c["label"]]
    name = protocols.norm(f"{when}{_bucket(c['at'])} {base}")[:40].strip()
    trigger = {"type": "time", "at": c["at"]}
    if c.get("days"):
        trigger["days"] = list(c["days"])
    return {"name": name, "steps": steps, "triggers": [trigger], "base": base,
            "learned": {"habit": key_of(c), "evidence": evidence_text(c),
                        "since": datetime.now().isoformat(timespec="seconds")}}


def _names_game(target: str, game: str) -> bool:
    """'apex legends' / 'apex' in a protocol step names the game 'Apex Legends'."""
    said, named = _key_app(target).split(), _key_app(game).split()
    return bool(said) and all(w in named for w in said)


def _display(c: dict) -> str:
    from core import protocols
    return protocols._app_name(c["subject"])


def evidence_text(c: dict) -> str:
    when = "" if c["label"] == "every day" else f" {c['label'].replace('on ', 'on ')}"
    return (f"you started {_display(c)} within {plural(WINDOW_MIN, 'minute')} of {_clock(c['at'])} on {c['hits']} of the "
            f"{plural(c['watched'], 'day')} I was watching{when} in the last {plural(LOOKBACK_DAYS, 'day')}")


def proposal_text(proto: dict, c: dict) -> str:
    from core import protocols
    usual = f"You usually start {_display(c)} around {_clock(c['at'])}" + ("" if c["label"] == "every day" else f" {c['label']}")
    steps = protocols._join([protocols.step_text(s) for s in proto["steps"]])
    what = (f"run the {proto['base']} protocol" if proto["base"] != _key_app(c["subject"]) else f"open {_display(c)}")
    return (f"{usual} ({c['hits']} of the last {plural(c['watched'], 'day')} I was watching). Want me to {what} "
            f"{'every day' if c['label'] == 'every day' else c['label']} at {_clock(c['at'])}? It would {steps}. "
            f"I saved it as the {proto['name']} protocol; it won't run until you say 'approve the {proto['name']} protocol'. "
            f"If you don't want it, say 'stop doing that' (or later 'delete the {proto['name']} protocol') and I won't "
            f"suggest it again.")


# -- state -----------------------------------------------------------------------------------------------------------
def _state() -> dict:
    s = storage.load(STATE, {})
    return s if isinstance(s, dict) else {}


def _save(s: dict) -> None:
    storage.save(STATE, s)


def declined() -> set:
    return {":".join(str(k).split(":")[:2]) for k in _state().get("declined", [])}


def decline(habit: str) -> None:
    habit = ":".join(str(habit).split(":")[:2])          # older keys carried the day set
    with _lock:
        s = _state()
        s["declined"] = sorted(set(s.get("declined", [])) | {habit})
        _save(s)


def learned_protocols() -> list[dict]:
    from core import protocols
    return [p for p in protocols.protocols() if p.get("learned")]


# -- the scheduler's daily pass (PC) ---------------------------------------------------------------------------------
def tick(now: datetime | None = None, deliver=None) -> str:
    """At most once a day (inside PROPOSE_HOURS): find a new habit and propose it. -> the sentence proposed, or ''."""
    from core import protocols, users
    now = now or datetime.now()
    if not PROPOSE_HOURS[0] <= now.hour < PROPOSE_HOURS[1]:
        return ""
    from core import pcbridge
    if not pcbridge.local_pc():
        return ""         # the PC proposes (it can check the app is installed); one writer of the proposals
    uid = protocols._owner()
    if uid is None:
        return ""
    with users.acting_as(uid):
        with _lock:
            s = _state()
            today = now.date().isoformat()
            if s.get("checked") == today:
                return ""
            s["checked"] = today
            _save(s)
        mine = learned_protocols()
        pending = [p for p in mine if not protocols.approved(p)
                   and now - _since(p) < timedelta(days=PENDING_DAYS)]
        if pending:
            return ""
        have = {p["learned"].get("habit") for p in mine}
        names = {p["name"] for p in protocols.protocols()}
        refused = declined()
        events, watched = evidence(now)
        for c in analyse(events, watched, now):
            if not c["habit"] or key_of(c) in have or key_of(c) in refused:
                continue
            proto = plan(c)
            if proto is None or proto["name"] in names:
                continue
            base = proto.pop("base")
            protocols._put({**proto, "created": now.isoformat(timespec="seconds")})
            text = proposal_text({**proto, "base": base}, c)
            with _lock:
                s = _state()
                s.setdefault("proposed", []).append({"habit": key_of(c), "name": proto["name"], "at": now.timestamp()})
                s["proposed"] = s["proposed"][-40:]
                _save(s)
            if _deliver(text, deliver, key=f"habit:{key_of(c)}") == "said":
                _set_referent(proto["name"])   # held for later: by then other turns happened; he deletes it by name
            return text
    return ""


def _since(p: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(p["learned"].get("since") or p.get("created")))
    except (TypeError, ValueError):
        return datetime.min


def _deliver(text: str, deliver=None, key: str = "") -> str:
    """-> 'said' | 'held' | '' (core.attention holds it while he's away or in a game)."""
    try:
        if deliver is not None:
            deliver(text)
            return "said"
        from core import attention
        return attention.deliver(text, attention.NORMAL, key=key)
    except Exception as error:  # noqa: BLE001
        print(f"[habits] deliver: {type(error).__name__}: {error}")
        return ""


def after_run(proto: dict, entry: dict) -> str:
    """What to say after a LEARNED protocol ran on its own: the first time, why and how to undo it; later, only a
    failure (he approved it; a daily 'all done' would be noise). '' = say nothing."""
    from core import protocols
    s = _state()
    told = set(s.get("told", []) if isinstance(s.get("told"), list) else [])
    mark = f"{proto['name']}#{protocols.fingerprint(proto)}"
    text = protocols.summary(entry)
    _set_referent(proto["name"])            # "stop doing that" now means this one (it opened something on screen)
    if mark not in told:
        with _lock:
            s = _state()
            s["told"] = sorted(told | {mark})[-80:]
            _save(s)
        return (f"I ran the {proto['name']} protocol on my own for the first time, as you approved. {text} "
                f"Say 'stop doing that' (or later 'delete the {proto['name']} protocol') if you don't want it.")
    if any(r.get("status") == protocols.FAILED for r in entry.get("steps", [])):
        return text
    return ""


# -- voice -----------------------------------------------------------------------------------------------------------
_STOP_THAT = re.compile(r"^\W*(?:(?:no|nope|jarvis|hey jarvis|ok|okay)[\s,]+)*(?:please\s+)?(?:stop doing (?:that|this|it)|"
                        r"don'?t do (?:that|this|it) (?:again|any ?more|every day)|do not do (?:that|this|it) (?:again|any ?more|every day)|"
                        r"stop (?:that|this) (?:habit|routine))(?:[\s,]+(?:any ?more|again|every day|please|jarvis))*\W*$", re.I)
_REPORT = re.compile(r"^\W*(?:(?:hey\s+)?jarvis[\s,]+)?(?:what|which|any|have you (?:noticed|learned|found)(?: any)?)\s*"
                     r"(?:habits?|routines?)(?:\s+(?:have you (?:noticed|learned|found|seen)|do (?:i|you see) have|"
                     r"of mine|did you (?:notice|find)|have i got|do you see))?\W*$"
                     r"|^\W*(?:(?:hey\s+)?jarvis[\s,]+)?what (?:have you learned|do you know) about my (?:routine|habits|day)\W*$"
                     r"|^\W*(?:(?:hey\s+)?jarvis[\s,]+)?(?:what are|tell me) my (?:habits|routines)\W*$"
                     r"|^\W*(?:(?:hey\s+)?jarvis[\s,]+)?(?:do you have )?any (?:protocol|habit|routine) suggestions(?: for me)?\W*$", re.I)


def _set_referent(name: str) -> None:
    """The learned protocol 'that' would mean: the one just suggested or just run (until any turn happens after it)."""
    with _lock:
        s = _state()
        s["referent"] = {"name": name, "at": time.time()}
        _save(s)


def _clear_referent() -> None:
    with _lock:
        s = _state()
        if s.pop("referent", None) is not None:
            _save(s)


_turn_started: dict = {}       # uid -> when this process last began a turn that was not "stop doing that" itself


def note_turn(uid: str, text: str) -> None:
    """Called at the start of every Brain turn (brain/llm.py _ask): some turns never leave a timing row (no audio, cut
    off, an error), so the rows alone missed them (review round 3, 2026-11-16)."""
    if not _STOP_THAT.match(_clean(text)):
        _turn_started[uid] = time.time()


def _clean(text) -> str:
    """The same line handle() reads: no phone/voice prefix, straight apostrophes (a phone types don’t)."""
    return re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(text or "").strip(), flags=re.I).replace("’", "'").strip()


def _turns_since(t: float) -> bool:
    """Was any turn (voice on the PC, the phone on either machine, anyone in the household) started after `t`? Every
    turn leaves a turn-timing row once it ends; the line being answered now has none yet. Reading the rows rather than
    clearing a flag in habits.handle: lines answered before it (taught commands, protocol lines, a bare yes/no resumed
    by the brain, long bursts) never reached that flag (review round 2, 2026-11-16)."""
    for host in ("pc", "server"):
        path = storage.DATA / "learning" / f"turn-timing-{host}.jsonl"
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                start = max(0, f.tell() - 64_000)
                f.seek(start)
                tail = f.read().decode("utf-8", "replace").splitlines()[1 if start else 0:]   # a cut first line only
        except OSError:
            continue
        for line in tail:
            try:
                if float(json.loads(line).get("at") or 0) > t:
                    return True
            except (ValueError, TypeError, AttributeError):
                continue
    return False


def _recent_target(now: float | None = None) -> dict | None:
    """The learned protocol 'that' means: the one suggested or run within STOP_WINDOW_S, with no turn by anyone in
    between (review 2026-11-16: a 6-hour window took 'stop doing that' meant for the companion or the music). When in
    doubt it is not ours: the line goes to the model, and 'delete the X protocol' always works."""
    now = now or time.time()
    ref = _state().get("referent") or {}
    try:
        at = float(ref.get("at") or 0) if isinstance(ref, dict) else 0.0
    except (TypeError, ValueError):
        at = 0.0
    if not at or now - at > STOP_WINDOW_S or max(list(_turn_started.values()), default=0.0) > at or _turns_since(at):
        return None          # any turn by anyone in this household since (fails toward "not ours")
    return next((p for p in learned_protocols() if p["name"] == ref.get("name")), None)


def stop(proto: dict) -> str:
    from core import protocols
    decline(proto["learned"].get("habit", ""))
    protocols._put(None, remove=proto["name"])
    _clear_referent()
    base = next((s for s in [p["name"] for p in protocols.protocols()] if proto["name"].endswith(" " + s)), "")
    tail = f" The {base} protocol itself is unchanged." if base else ""
    return (f"Done: I removed the {proto['name']} protocol, so it won't run on its own, and I won't suggest it again.{tail}")


def report(now: datetime | None = None) -> str:
    from core import protocols
    now = now or datetime.now()
    events, watched = evidence(now)
    found = analyse(events, watched, now)
    parts = []
    mine = learned_protocols()
    for p in mine:
        state = "approved and running" if protocols.approved(p) else "waiting for your approval"
        parts.append(f"The {p['name']} protocol is {state} ({p['learned'].get('evidence', '')}).")
    have = {p["learned"].get("habit") for p in mine}
    refused = declined()
    new = [c for c in found if c["habit"] and key_of(c) not in have and key_of(c) not in refused]
    for c in new[:2]:
        parts.append(f"You usually start {_display(c)} around {_clock(c['at'])}"
                     f"{'' if c['label'] == 'every day' else ' ' + c['label']} ({c['hits']} of {plural(c['watched'], 'day')}); "
                     "I'll suggest a protocol for it.")
    misses = [c for c in found if not c["habit"]][:3]
    for c in misses:
        parts.append(_miss_text(c))
    head = (f"I've been watching for {plural(len(watched), 'day')} in the last {plural(LOOKBACK_DAYS, 'day')} "
            "(your games, apps I saw you open, and what you asked me to open).")
    if not parts:
        return head + " I haven't seen anything you do at the same time on several days yet, so I have nothing to suggest."
    if not mine and not new:
        parts.append("Nothing is regular enough to suggest yet; I'll keep watching.")
    return head + " " + " ".join(parts)


def _miss_text(c: dict) -> str:
    """Why a near miss isn't a habit, in his terms (live check 2026-11-16 read '10 days, 3 of them near 8 PM, but only
    3 days near one time': true but it never said that his start times are spread out)."""
    name, days = _display(c), plural(c["event_days"], "day")
    on = "" if c["label"] == "every day" else " " + c["label"]
    first, last = (_clock(f"{m // 60:02d}:{m % 60:02d}") for m in (min(c["first_times"]), max(c["first_times"])))
    if c["watched"] < MIN_WATCHED:
        return f"{name}: {days}, but I've only been watching on {plural(c['watched'], 'day')}{on}, too few to say."
    if c["event_days"] < MIN_HITS:
        return f"{name}: only {days} so far; I need at least {plural(MIN_HITS, 'day')} to call it a routine."
    if c["hits"] < MIN_HITS:
        spread = f"you started it anywhere from {first} to {last}" if first != last else f"always around {first}"
        return (f"{name}: {days}, but {spread}; no more than {c['hits']} of those were within "
                f"{plural(WINDOW_MIN, 'minute')} of one time.")
    if c["share"] < MIN_SHARE:
        return (f"{name}: around {_clock(c['at'])}{on} on {c['hits']} of {plural(c['watched'], 'day')}, "
                "not often enough to call it usual.")
    return f"{name}: around {_clock(c['at'])}{on}, but not on any day in the last {plural(RECENT_DAYS, 'day')}."


def handle(text: str) -> str | None:
    raw = _clean(text)
    if not raw or len(raw) > 160:
        return None
    if _STOP_THAT.match(raw):
        from core import protocols
        if not protocols._is_owner():
            return None
        proto = _recent_target()
        return stop(proto) if proto is not None else None
    if _REPORT.match(raw):
        from core import protocols
        return report() if protocols._is_owner() else None
    return None


# -- watching which apps he opens (PC, every minute from the scheduler) ----------------------------------------------
def observe(now: float | None = None, windows=None) -> list[str]:
    """Record apps whose window newly appeared (process name only, no titles). The first look after a start only learns
    what is already open: an app open since before Jarvis started was not opened now. -> the names recorded."""
    global _prev_apps, _last_heartbeat, _first_look, _logged_day, _logged_today
    now = now or time.time()
    try:
        from core import pcbridge
        if not pcbridge.local_pc():
            return []
    except Exception:  # noqa: BLE001
        return []
    if windows is None:
        try:
            from tools import pcops
            windows = pcops.windows()
        except Exception:  # noqa: BLE001
            return []
    try:
        from tools import gaming
        games = {g.lower().removesuffix(".exe") for g in gaming.GAMES}
    except Exception:  # noqa: BLE001
        games = set()
    if not windows:
        return []             # an empty look is a failed look, never "everything closed" (the next look would record all)
    seen = set()
    for w in windows or []:
        name = _key_app(w.get("process") or "")
        if name and name not in _NOT_APPS and name not in games:
            seen.add(name)
    if _prev_apps is None:
        _first_look = now
    settling = now - _first_look < SETTLE_S
    new = [] if _prev_apps is None or settling else sorted(seen - _prev_apps)
    _prev_apps = seen
    day = date.fromtimestamp(now).isoformat()
    if _logged_day != day:
        _logged_day, _logged_today = day, set()
    new = [a for a in new if a not in _logged_today]       # the first open of the day is the habit; a flicker isn't news
    _logged_today.update(new)
    rows = [{"t": round(now, 1), "app": a} for a in new]
    if day != _last_heartbeat:
        rows.insert(0, {"t": round(now, 1), "app": None})      # a watched day, even one with no new app
        _last_heartbeat = day
    if not rows:
        return new
    from core import protocols, users
    uid = protocols._owner()
    if uid is None:
        return new
    with users.acting_as(uid):
        path = _app_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")
            if path.stat().st_size > APP_LOG_MAX * 60:
                keep = _jsonl(path)[-APP_LOG_MAX:]
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(r) + "\n" for r in keep)
        except OSError as error:
            print(f"[habits] app log: {error}")
    return new
