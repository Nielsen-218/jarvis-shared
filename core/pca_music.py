"""Jarvis picks the workout music (Project Captain America): pure rules, no storage, no network, no clock, no model.

His taste comes from his own words (the onboarding answer music_prefs) plus what he edits on the page; the part of the
workout picks a recipe of SOURCES (his artists, similar music, workout tracks, a motivational speech, an Army running
cadence, calm music for the cool-down); what he likes or skips moves the weights. The caller (tools/pca_music) fetches
search results (titles, ids, durations only) for the queries needs() names, and choose() picks from that pool with a
seeded random.Random, so a test can say exactly which song comes next. The language model never picks a song.

Events are tools/trainer music events in time order: {"type": "music", "act": "play"|"like"|"skip"|"neutral", "id",
"title", "channel", "duration_s", "source", "artist", "phase", "query", "date"}."""
import re

PHASES = ("warmup", "lift", "hard", "easy", "cooldown", "brief")
PHASE_WORDS = {"warmup": "warm-up", "lift": "lifting", "hard": "hard run", "easy": "easy run", "cooldown": "cool-down", "brief": "briefing"}
SOURCES = ("artist", "similar", "workout", "speech", "cadence", "calm", "lofi", "liked")
HARD_RUNS = ("fartlek", "tempo", "intervals", "race_pace", "sharpen", "time_trial", "test")
HINTS = ("artist", "surprise", "speech", "cadence")
DEFAULT_BPM = 175

# name -> (aliases he might say, genre tags). Aliases are matched as whole words in lower case.
ARTISTS = {
    "DaBaby": (("dababy", "da baby"), ("hip hop", "trap")),
    "Lil Baby": (("lil baby", "baby"), ("hip hop", "trap")),
    "Chief Keef": (("chief keef", "chief", "sosa"), ("drill", "trap", "hip hop")),
    "Sleepy Hallow": (("sleepy hallow", "sleepy hollow"), ("drill", "hip hop")),
    "Post Malone": (("post malone", "posty"), ("pop rap", "hip hop")),
    "Drake": (("drake",), ("hip hop", "pop rap")),
    "Kendrick Lamar": (("kendrick lamar", "kendrick"), ("hip hop",)),
    "J. Cole": (("j cole", "j. cole", "cole"), ("hip hop",)),
    "Travis Scott": (("travis scott", "travis"), ("hip hop", "trap")),
    "Future": (("future",), ("trap", "hip hop")),
    "21 Savage": (("21 savage", "savage"), ("trap", "hip hop")),
    "Lil Durk": (("lil durk", "durk"), ("drill", "hip hop")),
    "Polo G": (("polo g",), ("drill", "hip hop")),
    "Pop Smoke": (("pop smoke",), ("drill", "hip hop")),
    "Fivio Foreign": (("fivio foreign", "fivio"), ("drill",)),
    "Sheff G": (("sheff g",), ("drill",)),
    "Kay Flock": (("kay flock",), ("drill",)),
    "King Von": (("king von",), ("drill", "hip hop")),
    "Lil Tjay": (("lil tjay",), ("drill", "hip hop")),
    "Central Cee": (("central cee",), ("drill", "hip hop")),
    "G Herbo": (("g herbo", "herbo"), ("drill", "hip hop")),
    "Young Thug": (("young thug", "thug"), ("trap", "hip hop")),
    "Gunna": (("gunna",), ("trap", "hip hop")),
    "Lil Uzi Vert": (("lil uzi vert", "lil uzi", "uzi"), ("trap", "hip hop")),
    "Playboi Carti": (("playboi carti", "carti"), ("trap",)),
    "Migos": (("migos",), ("trap", "hip hop")),
    "Quavo": (("quavo",), ("trap", "hip hop")),
    "Offset": (("offset",), ("trap", "hip hop")),
    "Lil Wayne": (("lil wayne", "wayne", "weezy"), ("hip hop",)),
    "Eminem": (("eminem", "em"), ("hip hop",)),
    "50 Cent": (("50 cent", "fifty cent"), ("hip hop",)),
    "Kanye West": (("kanye west", "kanye", "ye"), ("hip hop",)),
    "Jay-Z": (("jay-z", "jay z", "jayz"), ("hip hop",)),
    "DMX": (("dmx",), ("hip hop",)),
    "Meek Mill": (("meek mill", "meek"), ("hip hop",)),
    "Rod Wave": (("rod wave",), ("hip hop", "r&b")),
    "NBA YoungBoy": (("nba youngboy", "youngboy", "nba young boy"), ("trap", "hip hop")),
    "Moneybagg Yo": (("moneybagg yo", "moneybagg"), ("trap", "hip hop")),
    "Kodak Black": (("kodak black", "kodak"), ("trap", "hip hop")),
    "Megan Thee Stallion": (("megan thee stallion", "megan"), ("hip hop",)),
    "Cardi B": (("cardi b", "cardi"), ("hip hop",)),
    "Nicki Minaj": (("nicki minaj", "nicki"), ("hip hop", "pop rap")),
    "Lil Nas X": (("lil nas x",), ("pop rap",)),
    "Jack Harlow": (("jack harlow",), ("pop rap", "hip hop")),
    "Juice WRLD": (("juice wrld", "juice world"), ("hip hop", "pop rap")),
    "XXXTentacion": (("xxxtentacion", "x"), ("hip hop",)),
    "Lil Yachty": (("lil yachty", "yachty"), ("trap",)),
    "Metro Boomin": (("metro boomin", "metro"), ("trap", "hip hop")),
    "Tyler, The Creator": (("tyler the creator", "tyler"), ("hip hop",)),
    "A$AP Rocky": (("asap rocky", "a$ap rocky"), ("hip hop",)),
    "Logic": (("logic",), ("hip hop",)),
    "NF": (("nf",), ("hip hop",)),
    "Tech N9ne": (("tech n9ne", "tech nine"), ("hip hop",)),
    "Lil Jon": (("lil jon",), ("hip hop",)),
    "The Weeknd": (("the weeknd", "weeknd"), ("r&b", "pop")),
    "Chris Brown": (("chris brown",), ("r&b",)),
    "SZA": (("sza",), ("r&b",)),
    "Bryson Tiller": (("bryson tiller",), ("r&b",)),
    "Summer Walker": (("summer walker",), ("r&b",)),
    "Usher": (("usher",), ("r&b",)),
    "Doja Cat": (("doja cat", "doja"), ("pop rap", "r&b")),
    "Calvin Harris": (("calvin harris",), ("edm",)),
    "David Guetta": (("david guetta", "guetta"), ("edm",)),
    "Martin Garrix": (("martin garrix", "garrix"), ("edm",)),
    "Skrillex": (("skrillex",), ("edm",)),
    "Marshmello": (("marshmello",), ("edm",)),
    "Avicii": (("avicii",), ("edm",)),
    "Linkin Park": (("linkin park",), ("rock",)),
    "Metallica": (("metallica",), ("metal", "rock")),
    "AC/DC": (("ac/dc", "acdc", "ac dc"), ("rock",)),
    "Imagine Dragons": (("imagine dragons",), ("rock", "pop")),
    "Disturbed": (("disturbed",), ("metal", "rock")),
    "Five Finger Death Punch": (("five finger death punch", "ffdp"), ("metal", "rock")),
    "Bad Bunny": (("bad bunny",), ("reggaeton", "latin")),
    "Burna Boy": (("burna boy", "burna"), ("afrobeats",)),
}
# genre words he might say -> the genre tag
GENRES = {"hip hop": "hip hop", "hip-hop": "hip hop", "hiphop": "hip hop", "rap": "hip hop", "trap": "trap", "drill": "drill",
          "pop rap": "pop rap", "r&b": "r&b", "rnb": "r&b", "r and b": "r&b", "edm": "edm", "electronic": "edm", "house": "edm",
          "dubstep": "edm", "rock": "rock", "metal": "metal", "pop": "pop", "country": "country", "latin": "latin",
          "reggaeton": "reggaeton", "afrobeats": "afrobeats", "phonk": "phonk", "gospel": "gospel", "jazz": "jazz"}
_GENRE_ORDER = ("hip hop", "trap", "drill", "pop rap", "r&b", "edm", "rock", "metal", "pop", "phonk", "latin", "reggaeton", "afrobeats")
_OPEN = re.compile(r"\b(?:and more|idc|i don'?t care|good music|anything|whatever|open to|similar|all kinds|everything|etc)\b", re.I)
_NEG = re.compile(r"\b(?:not|no|never|anything but|except|but not|hate|don'?t like|do not like|nothing)\s+(?P<what>[a-z&$ /'-]{2,30}?)(?=\s+(?:tho|though|lol|lmao|haha|please|pls|music|at all)\b|[,.;!]|$)", re.I)
_FILLER = {"i", "like", "love", "good", "music", "more", "idc", "tho", "though", "lol", "lmao", "and", "or", "the", "a", "an", "some",
           "stuff", "songs", "song", "really", "anything", "whatever", "care", "dont", "don't", "do", "not", "no", "but", "etc",
           "mostly", "mainly", "also", "too", "artists", "artist", "rappers", "rapper", "listen", "to", "is", "are", "all", "kinds",
           "of", "my", "favorite", "favourite", "hype", "workout", "gym", "best", "similar", "open", "everything", "with", "else"}


def _norm(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"[^a-z0-9&$/',;\n -]+", " ", str(text or "").lower())).strip()


def _find(text: str, alias: str):
    return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text)


def known(name: str) -> str:
    """The table's spelling of an artist he names ('sleepy hollow' -> 'Sleepy Hallow'), else the name as he wrote it."""
    said = _norm(name)
    for real, (aliases, _g) in ARTISTS.items():
        if said == _norm(real) or said in aliases:
            return real
    return re.sub(r"\s+", " ", str(name or "")).strip()[:40]


def genres_of(artists: list) -> list[str]:
    """The genres his artists share, most common first (hip hop, trap, drill for his list); unknown artists add none."""
    count: dict[str, int] = {}
    for a in artists:
        for g in (ARTISTS.get(known(a)) or ((), ()))[1]:
            count[g] = count.get(g, 0) + 1
    return sorted(count, key=lambda g: (-count[g], _GENRE_ORDER.index(g) if g in _GENRE_ORDER else 99))[:3]


def _str_list(value, limit: int = 30) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        s = re.sub(r"\s+", " ", str(v)).strip()[:40] if isinstance(v, (str, int, float)) else ""
        if s and s.lower() not in [o.lower() for o in out]:
            out.append(s)
    return out[:limit]


def taste(prefs_text: str, saved: dict | None = None) -> dict:
    """His taste from his own words plus the page's saved lists: {"artists", "genres", "avoid", "open", "cadence", "tempo"}.
    Saved lists (artists, genres, avoid) replace what the text gave; saved open/cadence/tempo switches win."""
    text = _norm(prefs_text)
    avoid: list[str] = []
    for m in _NEG.finditer(text):
        what = m["what"].strip()
        tag = GENRES.get(what) or next((GENRES[w] for w in GENRES if _find(what, w)), None)
        hit = tag or next((real for real, (aliases, _g) in ARTISTS.items() if any(_find(what, a) for a in aliases if len(a) > 2)), None)
        if hit and hit not in avoid:
            avoid.append(hit)
    clean = _NEG.sub(" ", text)
    found: list[tuple[int, str]] = []
    rest = clean
    for real, (aliases, _g) in ARTISTS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            m = _find(rest, alias)
            if m and real not in avoid and alias not in ("em", "x", "ye"):     # too short to trust in free text
                found.append((m.start(), real))
                rest = rest[:m.start()] + " " * len(alias) + rest[m.end():]       # 'dababy' then 'baby': each word once
                break
    genres_said: list[str] = []
    for word in sorted(GENRES, key=len, reverse=True):
        m = _find(rest, word)
        if m:
            g = GENRES[word]
            if g not in avoid and g not in genres_said:
                genres_said.append(g)
            rest = rest[:m.start()] + " " * len(word) + rest[m.end():]
    artists = [a for _p, a in sorted(found)]
    for chunk in re.split(r"[,;/]| and | & ", rest):         # names the table does not know, kept as he wrote them
        words = chunk.split()
        left = [w for w in words if w not in _FILLER and w not in GENRES]
        if left and len(left) <= 4 and len(words) <= 6:
            name = " ".join(w if any(ch.isdigit() for ch in w) else w.capitalize() for w in left)
            if len(name) > 1 and name.lower() not in [a.lower() for a in artists + avoid]:
                artists.append(name[:40])
    saved = saved if isinstance(saved, dict) else {}
    if isinstance(saved.get("artists"), list):
        artists = [known(a) for a in _str_list(saved["artists"])]
    if isinstance(saved.get("avoid"), list):
        avoid = [GENRES.get(_norm(a), known(a) if _norm(a) not in GENRES else a) for a in _str_list(saved["avoid"])]
    genres = genres_said + [g for g in genres_of(artists) if g not in genres_said]
    if isinstance(saved.get("genres"), list):
        genres = [GENRES.get(_norm(g), _norm(g)) for g in _str_list(saved["genres"], 8)]
    avoid_l = [a.lower() for a in avoid]
    genres = [g for g in genres if g.lower() not in avoid_l][:6]
    artists = [a for a in artists if a.lower() not in avoid_l]
    is_open = bool(_OPEN.search(text)) if "open" not in saved else saved.get("open") in (True, "true", "on", "yes", 1)
    flag = lambda k: saved.get(k, True) not in (False, "false", "off", "no", 0)               # noqa: E731
    return {"artists": artists, "genres": genres or (["hip hop"] if not avoid_l or "hip hop" not in avoid_l else []),
            "avoid": avoid, "open": is_open, "cadence": flag("cadence"), "tempo": flag("tempo")}


# ---- recipes ---------------------------------------------------------------------------------------------------------
def tempo_run(ctx: dict | None) -> bool:
    return str((ctx or {}).get("run_kind") or "") in HARD_RUNS


def recipe(phase: str, t: dict, ctx: dict | None = None) -> dict:
    """The starting weights of each source for this part of the workout (before what he liked or skipped)."""
    ctx = ctx or {}
    run_day = bool(ctx.get("run"))
    if phase == "brief":
        w = {"speech": 0.7, "cadence": 0.3}
    elif phase == "cooldown":
        w = {"calm": 0.6, "lofi": 0.4}
    elif phase == "hard":
        w = {"artist": 0.30, "similar": 0.20, "workout": 0.40, "cadence": 0.10}
    elif phase == "easy":
        w = {"artist": 0.35, "similar": 0.25, "workout": 0.15, "speech": 0.25, "cadence": 0.15}
    elif phase == "lift":
        w = {"artist": 0.45, "similar": 0.25, "workout": 0.20, "speech": 0.10}
    else:                                                    # warm-up (and a rest day)
        w = {"artist": 0.45, "similar": 0.25, "workout": 0.30}
        if run_day:
            w["cadence"] = 0.10
    if not t.get("artists"):
        w.pop("artist", None)
        w.pop("calm", None) if not t.get("genres") else None
    if not t.get("open"):
        w.pop("similar", None)
    if not t.get("cadence"):
        w.pop("cadence", None)
    return {k: v for k, v in w.items() if v > 0}


def _bpm(ctx: dict | None) -> int:
    try:
        n = int((ctx or {}).get("bpm") or 0)
    except (TypeError, ValueError):
        n = 0
    return n if 140 <= n <= 200 else 0


def queries(source: str, phase: str, t: dict, artist: str = "", ctx: dict | None = None) -> list[str]:
    """The search strings for one source, in the order they are tried. An avoided genre is never searched."""
    genres = [g for g in t.get("genres") or [] if g not in (t.get("avoid") or [])] or ["hip hop"]
    artists = [artist] if artist else list(t.get("artists") or [])
    bpm = _bpm(ctx)
    if source == "artist":
        return [q for a in artists for q in (f"{a} official audio", f"{a} best songs")]
    if source == "similar":
        return [f"{g} hype gym" for g in genres] + [f"artists like {a}" for a in artists[:3]]
    if source == "workout":
        if (phase == "hard" or tempo_run(ctx)) and t.get("tempo", True):
            n = bpm or 170
            return [f"tempo run music {n} bpm"] + [q for g in genres[:2] for q in (f"running music {bpm or 175} bpm {g}", f"{bpm or 180} bpm running {g}")]
        if phase == "hard":
            return [f"running music {g}" for g in genres[:2]]
        return [q for g in genres for q in (f"{g} workout music", f"gym motivation {g}")]
    if source == "speech":
        return ["motivational speech workout", "discipline motivational speech", "military motivation speech"]
    if source == "cadence":
        return ["army running cadence", "army cadence call run", "airborne ranger running cadence", f"military cadence {bpm or 180} bpm run"]
    if source == "calm":
        return [f"chill {g}" for g in genres[:2]] + [f"{a} slow songs" for a in artists[:3]]
    if source == "lofi":
        return ["slow r&b", "calm r&b", "lo-fi beats"]
    return []


# ---- what is acceptable ----------------------------------------------------------------------------------------------
# Exact spellings of ONE term (never a subgenre widened to its family): avoiding any spelling avoids them all. Rap is
# hip hop's other name here. His own words are always matched literally as well, so "house" drops "House Party".
SPELLINGS = (("hip hop", "hiphop", "rap"), ("r&b", "r & b", "rnb", "r and b", "r n b"), ("afrobeats", "afrobeat"),
             ("lo fi", "lofi"), ("k pop", "kpop"))


def _flat(text: str) -> str:
    """Lower case, every run of punctuation, hyphens and spaces made one space ('Hip-Hop' -> 'hip hop'); '&' is kept."""
    return re.sub(r"[^a-z0-9&]+", " ", str(text or "").lower()).strip()


def _spellings(word: str) -> set:
    w = _flat(word)
    out = {w}
    for group in SPELLINGS:
        flat = {_flat(g) for g in group}
        if w in flat:
            out |= flat
    return {x for x in out if x}


def avoided(text: str, avoid: list) -> str:
    """The avoided word (genre or artist, as he wrote it) this title or channel shows as a whole word, else ''."""
    flat = " " + _flat(text) + " "
    for a in avoid or []:
        for v in _spellings(str(a)):
            if re.search(r"(?<![a-z0-9&])" + re.escape(v) + r"(?![a-z0-9&])", flat):
                return str(a)
    return ""


_BANNED = ("full album", "playlist", "hour", "hours", "reaction", "review", "live stream", "livestream", "karaoke", "instrumental",
           "slowed", "sped up", "8d", "nightcore", "podcast", "interview", "trailer")


def norm_title(title: str) -> str:
    t = re.sub(r"[\(\[].*?[\)\]]", " ", str(title or "").lower())
    t = re.sub(r"\b(?:official|audio|video|music|lyrics?|visuali[sz]er|hd|hq|explicit|clean)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def acceptable(c: dict, phase: str, source: str, t: dict, seen: set | None = None) -> tuple[bool, str]:
    """Is this search result fit to play now? -> (ok, reason). Songs 1.5-7 min; speeches 2-20 min; Army cadences 1.5-30 min;
    the briefing's speech or cadence under 6 min. Compilations, reactions, edits and avoided genres are dropped."""
    if not isinstance(c, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(c.get("id") or "")):
        return False, "no video id"
    title = str(c.get("title") or "")
    low = (title + " " + str(c.get("channel") or "")).lower()
    try:
        secs = float(c.get("duration_s"))
    except (TypeError, ValueError):
        return False, "unknown length"
    if seen is not None and (c["id"] in seen or norm_title(title) in seen):
        return False, "duplicate"
    if avoided(low, t.get("avoid") or []):
        return False, f"avoided: {avoided(low, t.get('avoid') or [])}"
    words = re.sub(r"[^a-z0-9& ]+", " ", title.lower())
    for b in _BANNED:
        if b == "instrumental" and phase == "cooldown":
            continue
        if b in ("hour", "hours") and source == "cadence":
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(b) + r"(?![a-z0-9])", words):
            return False, f"banned word: {b}"
    if re.search(r"\b(?:mix|compilation)\b", words) and source != "cadence" and secs > 720:
        return False, "long mix"
    speechy = re.search(r"\b(?:speech|motivational|motivation video)\b", words)
    if source not in ("speech",) and speechy and source != "cadence":
        return False, "a speech, not a song"
    if source == "cadence" and not re.search(r"\b(?:cadences?|jodys?|jodies)\b", words):
        return False, "not a cadence"
    if phase == "brief":
        lo, hi = (90, 359) if source == "cadence" else (60, 359)
    elif source == "speech":
        lo, hi = 120, 1200
    elif source == "cadence":
        lo, hi = 90, 1800
    else:
        lo, hi = 90, 420
    if not lo <= secs <= hi:
        return False, f"length {int(secs)} s outside {lo}-{hi} s"
    return True, "ok"


# ---- learning --------------------------------------------------------------------------------------------------------
def _music(events: list) -> list[dict]:
    return [e for e in events or [] if isinstance(e, dict) and e.get("type", "music") == "music" and e.get("act")]


def weights(phase: str, events: list, t: dict | None = None, ctx: dict | None = None) -> dict:
    """Each source's weight now: the recipe x what he did in this phase (Like x1.25, Skip x0.85, neutral x1.03; x0.3 to x3).
    A source at the floor keeps a 5% share so he can drift back."""
    base = recipe(phase, t or {"artists": ["x"], "genres": ["hip hop"], "open": True, "cadence": True}, ctx)
    mult = {s: 1.0 for s in base}
    for e in _music(events):
        s = e.get("source")
        if e.get("phase") != phase or s not in mult:
            continue
        f = {"like": 1.25, "unlike": 1 / 1.25, "skip": 0.85, "neutral": 1.03}.get(e["act"])
        if f:
            mult[s] = min(3.0, max(0.3, mult[s] * f))
    w = {s: base[s] * mult[s] for s in base}
    total = sum(w.values()) or 1.0
    return {s: max(v, 0.05 * total) for s, v in w.items()}


def artist_weights(phase: str, events: list, t: dict, today: str = "") -> dict:
    """Each listed artist's weight (Like x1.3 up to 3.0; Skip in this phase x0.8 down to 0.3); an artist skipped twice in a row
    today rests for the rest of the day (weight 0)."""
    w = {a: 1.0 for a in t.get("artists") or []}
    for e in _music(events):
        a = e.get("artist")
        if a not in w:
            continue
        if e["act"] == "like":
            w[a] = min(3.0, w[a] * 1.3)
        elif e["act"] == "unlike":
            w[a] = max(0.3, w[a] / 1.3)
        elif e["act"] == "skip" and e.get("phase") == phase:
            w[a] = max(0.3, w[a] * 0.8)
    for a in resting(events, today):
        if a in w:
            w[a] = 0.0
    return w


def resting(events: list, today: str = "") -> set:
    """Artists whose last two fates today were both skips."""
    fates = [e for e in _music(events) if e["act"] in ("like", "skip", "neutral") and (not today or str(e.get("date")) == today)]
    out, last = set(), {}
    for e in fates:
        a = e.get("artist")
        if not a:
            continue
        if e["act"] == "skip" and last.get(a) == "skip":
            out.add(a)
        last[a] = e["act"]
    return out


def history(events: list) -> dict:
    """What the picker must respect: the last 40 plays (ids and titles), the skipped songs (never again), the liked pool."""
    ev = _music(events)
    plays = [e for e in ev if e["act"] == "play"]
    recent = set()
    for e in plays[-40:]:
        recent |= {e.get("id"), norm_title(e.get("title"))}
    skipped = {e.get("id") for e in ev if e["act"] == "skip"} | {norm_title(e.get("title")) for e in ev if e["act"] == "skip" and e.get("title")}
    liked: dict[str, dict] = {}
    info = {e.get("id"): e for e in plays if e.get("source") != "liked"}     # what the song first was (a song, a speech...)
    for e in ev:
        if e["act"] == "like" and e.get("id"):
            src = info.get(e["id"]) or e
            liked[e["id"]] = {k: src.get(k) for k in ("id", "title", "channel", "duration_s", "artist", "source")}
            if liked[e["id"]].get("source") in (None, "", "liked"):
                liked[e["id"]]["source"] = "artist"
        elif e["act"] in ("skip", "unlike"):
            liked.pop(e.get("id"), None)
    last25 = {e.get("id") for e in plays[-25:]}
    return {"recent": recent - {None, ""}, "skipped": skipped - {None, ""}, "liked": liked, "last25": last25,
            "liked_recently": any(e.get("source") == "liked" for e in plays[-5:]), "plays": len(plays)}


# ---- the pick --------------------------------------------------------------------------------------------------------
def _why(source: str, phase: str, artist: str, t: dict, ctx: dict | None) -> str:
    where = PHASE_WORDS.get(phase, "workout")
    genre = (t.get("genres") or ["hip hop"])[0]
    if source == "artist":
        return f"Because you listed {artist}."
    if source == "similar":
        return f"{genre[0].upper() + genre[1:]} like the artists you listed."
    if source == "workout":
        if (phase == "hard" or tempo_run(ctx)) and t.get("tempo", True):
            return f"A {_bpm(ctx) or 175} bpm track for the {'tempo run' if tempo_run(ctx) else where}."
        return f"A workout track for the {where}."
    if source == "speech":
        return "A short speech before you start." if phase == "brief" else f"A motivational speech for the {where}."
    if source == "cadence":
        return "A short Army cadence before you start." if phase == "brief" else "An Army running cadence for the run."
    if source in ("calm", "lofi"):
        return "Something calm for the cool-down."
    return "You liked this one before."


def _candidates(source: str, phase: str, t: dict, pool: dict, hist: dict, artist: str = "", ctx: dict | None = None,
                allowed=()) -> list[tuple]:
    seen = set(hist["recent"]) | set(hist["skipped"])
    out = []
    if source == "liked":                                    # a liked song passes the same filter as a new one, as what it was
        for c in hist["liked"].values():
            was = c.get("source") or "artist"
            if was not in allowed or c.get("id") in hist["last25"] or c.get("id") in hist["skipped"]:
                continue
            if acceptable(c, phase, was, t, set(hist["last25"]) | set(hist["skipped"]))[0]:
                out.append((c, "", c.get("artist") or ""))
        return out
    for q in queries(source, phase, t, artist, ctx):
        for c in pool.get(q) or []:
            ok, _ = acceptable(c, phase, source, t, seen)
            if ok:
                out.append((c, q, artist))
                seen |= {c["id"], norm_title(c.get("title"))}
    return out


def _weighted(rng, items: dict):
    items = {k: v for k, v in items.items() if v > 0}
    if not items:
        return None
    total = sum(items.values())
    r = rng.random() * total
    for k in sorted(items):
        r -= items[k]
        if r < 0:
            return k
    return sorted(items)[-1]


def choose(phase: str, t: dict, events: list, pool: dict, rng, ctx: dict | None = None, hint: str = "", today: str = "") -> dict | None:
    """The next song: a weighted source, then an artist or a query, then the first acceptable result in the pool that was not
    among the last 40 plays and was never skipped. None when the pool has nothing fit (the caller says so honestly)."""
    phase = phase if phase in PHASES else "warmup"
    hist = history(events)
    w = weights(phase, events, t, ctx)
    if hist["liked"] and not hist["liked_recently"] and phase not in ("cooldown", "brief"):
        w["liked"] = 0.12 * sum(w.values())
    aw = artist_weights(phase, events, t, today)
    per: dict[str, list] = {}
    for s in w:
        if s == "artist":
            for a in t.get("artists") or []:
                if aw.get(a, 0) > 0:
                    per.setdefault("artist", []).extend(_candidates("artist", phase, t, pool, hist, a, ctx))
        else:
            per[s] = _candidates(s, phase, t, pool, hist, "", ctx, tuple(k for k in w if k != "liked"))
    want = {"surprise": "similar" if "similar" in w else "workout"}.get(hint, hint)
    if want == "speech" and phase == "hard":
        want = ""
    if want in ("speech", "cadence") and want not in w and phase != "cooldown":
        per[want] = _candidates(want, "lift" if want == "speech" else "easy", t, pool, hist, "", ctx)
        w[want] = 1.0
    if want and per.get(want):
        source = want
    else:
        source = _weighted(rng, {s: v for s, v in w.items() if per.get(s)})
    if not source:
        return None
    cands = per[source]
    artist = ""
    if source == "artist":
        names = {a: aw[a] for a in {c[2] for c in cands} if aw.get(a, 0) > 0}
        artist = _weighted(rng, names) or ""
        cands = [c for c in cands if c[2] == artist]
    order = sorted({c[1] for c in cands}, key=lambda q: [x[1] for x in cands].index(q))
    rng.shuffle(order)
    c, q, a = next(x for x in cands if x[1] == order[0])
    return {"id": c["id"], "title": str(c.get("title") or "")[:200], "channel": str(c.get("channel") or "")[:120],
            "duration_s": int(float(c.get("duration_s") or 0)), "source": source, "artist": a or artist, "query": q,
            "phase": phase, "why": _why(source, phase, a or artist, t, ctx)}


def needs(phase: str, t: dict, events: list, pool: dict, ctx: dict | None = None, hint: str = "", limit: int = 3) -> list[str]:
    """The searches the caller must run (at most `limit`) so each likely source has at least 2 fresh candidates; the picker
    itself never touches the network. Heaviest sources first; for his artists, the two heaviest artists without results."""
    phase = phase if phase in PHASES else "warmup"
    hist = history(events)
    w = weights(phase, events, t, ctx)
    want = {"surprise": "similar" if "similar" in w else "workout"}.get(hint, hint)
    if want in ("speech", "cadence") and want not in w and phase not in ("cooldown", "hard"):
        w[want] = 9.0
    elif want in w:
        w[want] = 9.0
    aw = artist_weights(phase, events, t)
    per: list[list[str]] = []
    for s in sorted(w, key=lambda s: -w[s]):
        if s == "liked":
            continue
        groups = sorted((a for a in aw if aw[a] > 0), key=lambda a: (-aw[a], list(aw).index(a))) if s == "artist" else [""]
        mine: list[str] = []
        for a in groups[:3]:
            have = len(_candidates(s, phase, t, pool, hist, a, ctx))
            for q in queries(s, phase, t, a, ctx):
                if have >= 2:
                    break
                if q not in pool:
                    mine.append(q)
                    have += 2                              # a search usually brings several; check again next time
        per.append(mine)
    out: list[str] = []
    for i in range(max((len(x) for x in per), default=0)):   # one search per source first, so the first pick can be any of them
        out += [x[i] for x in per if i < len(x) and x[i] not in out]
    return out[:limit]
