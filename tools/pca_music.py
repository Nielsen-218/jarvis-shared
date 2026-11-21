"""Jarvis picks the workout music for Project Captain America: search, cache, events and the page/voice API.

The rules are core/pca_music (pure). This module reads his taste (the onboarding answer music_prefs plus the page's
saved lists under fitness.json pca.music), fetches YouTube search results for the queries the rules name, and records
what he plays, likes and skips as trainer events ({"type": "music", ...}), so the PC and the laptop never overwrite
each other. Only titles, ids and durations are read: no audio or video is ever downloaded, streamed or embedded; the
song plays in HIS app (Metrolist / YouTube Music) from a link he taps, or, when his phone runs the Jarvis Link helper
app (tools/pca_link), Jarvis plays, pauses, skips and likes it there itself (the "Jarvis Link" section at the end, with
the auto-DJ that runs the music during a workout).

Search: the yt_dlp Python module when it is importable (the laptop), else the yt-dlp executable through tools/reach's
locator (the PC), flat "ytsearch8:<query>" with an 8 s limit. Any failure returns [] and is remembered for 10 minutes so
a broken network never slows the page. Results are cached per query in fitness/music-cache.json (14 days, 400 queries).

Metrolist (checked in its source, MetrolistGroup/Metrolist 0a9dbcaa5f, 2026-11-20): a VIEW of a music.youtube.com
/watch?v=<id> link plays that one song (IncomingDestination.PlayVideo(id, null): any &list= is dropped), as a finite
one-song queue; with its setting "Add similar songs automatically" (autoLoadMore, default on) the app then adds similar
songs itself. So the link form is the plain watch link ("watch")."""
from core.words import plural
import json
import math
import random
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote, quote_plus

from core import pca_music as rules
from core import storage
from tools import fitness, trainer

CACHE = "fitness/music-cache.json"
STATE = "fitness/music-state.json"
CACHE_DAYS = 14
CACHE_MAX = 400
FAIL_S = 600
TIMEOUT_S = 8                            # one search, wall clock
TOTAL_S = 10                             # all the searches of one pick, wall clock
EMPTY_S = 1800                           # an empty answer is not asked again for 30 minutes
LINK_FORM = "watch"
DJ_SERVICES = ("metrolist", "youtube_music")
_failed: dict[str, float] = {}           # "search" -> monotonic time of the last failure
_cache_lock = threading.RLock()          # searches run side by side; the cache file is written one at a time


# ---- search ----------------------------------------------------------------------------------------------------------
def _row(e: dict) -> dict | None:
    vid = str(e.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
        return None
    try:
        dur = int(float(e.get("duration"))) if e.get("duration") is not None else None
    except (TypeError, ValueError):
        dur = None
    return {"id": vid, "title": str(e.get("title") or "")[:200], "channel": str(e.get("channel") or e.get("uploader") or "")[:120],
            "duration_s": dur}


def _backend(query: str, n: int) -> list[dict]:
    """The real search: titles, ids and durations only (flat: nothing is downloaded)."""
    target = f"ytsearch{n}:{query}"
    try:
        import yt_dlp                                        # the laptop
    except ImportError:
        yt_dlp = None
    if yt_dlp is not None:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "socket_timeout": TIMEOUT_S}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False) or {}
        return [r for r in (_row(e) for e in info.get("entries") or [] if isinstance(e, dict)) if r]
    from tools import reach                                  # the PC: the yt-dlp program
    r = reach._run([reach._exe("yt-dlp"), "--flat-playlist", "--dump-json", "--no-warnings", "--socket-timeout", str(TIMEOUT_S),
                    target], timeout=TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "yt-dlp failed")[:200])
    out = []
    for line in (r.stdout or "").splitlines():
        try:
            row = _row(json.loads(line))
        except ValueError:
            continue
        if row:
            out.append(row)
    return out


def _cache(cache: str = CACHE) -> dict:
    with _cache_lock:                                        # never read while another search's write replaces the file
        try:
            data = storage.load(cache, {})
        except OSError:
            data = {}
    return data if isinstance(data, dict) else {}


def _fresh(hit) -> list[dict] | None:
    try:
        keep = timedelta(days=CACHE_DAYS) if hit.get("results") else timedelta(seconds=EMPTY_S)
        fresh = isinstance(hit, dict) and trainer.clock() - datetime.fromisoformat(hit["t"]) < keep
    except (TypeError, ValueError, KeyError, AttributeError):
        fresh = False
    return list(hit.get("results") or []) if fresh else None


def cached(query: str, cache: str = CACHE) -> list[dict] | None:
    return _fresh(_cache(cache).get(query))


def search(query: str, n: int = 8, cache: str = CACHE) -> list[dict]:
    """[{"id", "title", "channel", "duration_s"}] for a YouTube search, from the cache when it is fresh. The backend runs
    in a worker thread with a hard TIMEOUT_S cap (a hung search is abandoned, never waited for). [] on any failure or
    timeout, and then no new attempt for 10 minutes; an empty answer is remembered for 30 minutes. cache names the cache
    file: tools/pca_demo keeps its exercise videos in fitness/demo-cache.json, so they can never be picked as songs."""
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:120]
    if not query:
        return []
    hit = cached(query, cache)
    if hit is not None:
        return hit
    if time.monotonic() - _failed.get("search", -1e9) < FAIL_S:
        return []
    box: dict = {}

    def run():
        try:
            box["rows"] = _backend(query, n)
        except Exception as e:                               # noqa: BLE001 - no network, no yt-dlp: the page goes on
            box["err"] = e
    worker = threading.Thread(target=run, daemon=True, name="pca-music-search")
    worker.start()
    worker.join(TIMEOUT_S)
    if worker.is_alive() or "err" in box or not isinstance(box.get("rows"), list):
        _failed["search"] = time.monotonic()
        return []
    rows = box["rows"]
    with _cache_lock:
        data = _cache(cache)
        data[query] = {"t": trainer.clock().isoformat(timespec="seconds"), "results": rows}
        if len(data) > CACHE_MAX:
            for old in sorted(data, key=lambda k: str(data[k].get("t")))[:len(data) - CACHE_MAX]:
                data.pop(old)
        storage.save(cache, data)
    return rows


def search_all(queries: list[str], n: int = 8, cache: str = CACHE) -> dict:
    """Run several searches side by side for at most TOTAL_S seconds in all: {query: rows}. A search still running at the
    deadline is left behind (its thread is a daemon) and counts as no result."""
    from core import users
    uid, found = users.current(), {}

    def one(q):
        with users.acting_as(uid):                           # the cache is the person's own
            found[q] = search(q, n, cache)
    threads = [threading.Thread(target=one, args=(q,), daemon=True, name="pca-music-search-all") for q in queries]
    for th in threads:
        th.start()
    deadline = time.monotonic() + TOTAL_S
    for th in threads:
        th.join(max(0.0, deadline - time.monotonic()))
    return {q: list(found.get(q) or []) for q in queries}


# ---- his taste, the phase, the links ---------------------------------------------------------------------------------
def _pca_state(data: dict) -> dict:
    from tools import pca
    return pca._state(data)


def taste_now(data: dict | None = None) -> dict:
    from tools import pca
    data = data if data is not None else fitness._data()
    said = str(pca._answers(data).get("music_prefs") or "")
    return rules.taste(said, _pca_state(data).get("music") or {})


def prefs(data: dict | None = None) -> dict:
    data = data if data is not None else fitness._data()
    return _pca_state(data).get("audio") or {}


def enabled(p: dict) -> bool:
    """Is the DJ switched on? On by default for metrolist and youtube_music; never for none, local or no service."""
    service = str(p.get("service") or "")
    dj = p.get("dj", service in DJ_SERVICES)
    return service not in ("none", "local", "") and dj not in (False, None, "", "false", "off", "no", 0)


OFF = "Music picking is off. Turn it on in audio settings."
NO_PROGRAM = "Music picking is part of Project Captain America, and the program is not running."


def gate(data: dict | None = None) -> str:
    """The one switch every entry point asks (page actions, skills, voice): '' when Jarvis may pick, else the sentence."""
    data = data if data is not None else fitness._data()
    if not _is_pca(data):
        return NO_PROGRAM
    return "" if enabled(prefs(data)) else OFF


def link(song: dict | None, service: str) -> str | None:
    """Where the song opens: the YouTube Music watch link (Metrolist and YouTube Music play it); Spotify and Apple Music
    cannot take a YouTube id, so they get a search for the title and channel."""
    if not song or not song.get("id"):
        return None
    if service == "spotify":
        return "https://open.spotify.com/search/" + quote(f"{song.get('title', '')} {song.get('channel', '')}".strip())
    if service == "apple_music":
        return "https://music.apple.com/search?term=" + quote_plus(f"{song.get('title', '')} {song.get('channel', '')}".strip())
    if service == "local":
        return None
    return f"https://music.youtube.com/watch?v={song['id']}"


def search_link(query: str) -> str:
    return "https://music.youtube.com/search?q=" + quote_plus(query)


def _bpm(value) -> int:
    try:
        f = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(f):
        return 0
    return int(f) if 140 <= f <= 200 else 0


def _ctx(day: dict | None, run_kind=None, bpm=0) -> dict:
    items = (day or {}).get("items") or []
    runs = [i.get("run") or {} for i in items if i.get("run")]
    kind = str(run_kind if run_kind is not None else (runs[0].get("type") if runs else "") or "")[:20]
    return {"run": bool(runs) or bool(run_kind), "run_kind": kind, "bpm": _bpm(bpm)}


def _phase(phase: str, day: dict | None) -> str:
    from tools import pca
    phase = str(phase or "").strip().lower()
    if phase not in rules.PHASES:
        phase = pca.audio_phase(day or {}) if day is not None else "warmup"
    return phase if phase in rules.PHASES else "warmup"


def _music_events() -> list[dict]:
    return [e for e in trainer.events() if e.get("type") == "music"]


def _state() -> dict:
    s = storage.load(STATE, {})
    return s if isinstance(s, dict) else {}


def _record(act: str, song: dict, phase: str = "") -> bool:
    ev = {"type": "music", "act": act, **{k: song.get(k) for k in ("id", "title", "channel", "duration_s", "source", "artist", "query")},
          "phase": phase or song.get("phase") or ""}
    return not trainer.record(ev).get("unsaved")


def _say(song: dict) -> str:
    by = f" by {song['channel']}" if song.get("channel") else ""
    return f"{song['title']}{by}. {song['why']}"


def _pool() -> dict:
    """Every fresh cached search (an empty answer counts: it is not asked again for 30 minutes)."""
    pool = {}
    for q, hit in _cache().items():
        rows = _fresh(hit)
        if rows is not None:
            pool[q] = rows
    return pool


def _choose(phase, t, events, pool, ctx, hint, today, salt) -> dict | None:
    rng = random.Random(f"{today}:{len([e for e in events if e.get('act') == 'play'])}:{salt}")
    return rules.choose(phase, t, events, pool, rng, ctx, hint, today)


def _top_query(phase, t, events, ctx, want) -> str:
    if want:
        return want[0]
    w = rules.weights(phase, events, t, ctx)
    top = max(w, key=w.get) if w else "workout"
    return (rules.queries(top, phase, t, (t.get("artists") or [""])[0] if top == "artist" else "", ctx) or ["workout music"])[0]


# ---- the API ---------------------------------------------------------------------------------------------------------
def _off(message: str) -> dict:
    return {"ok": False, "message": message, "song": None, "upnext": None}


def _pick(phase: str = "", reason: str = "start", source: str = "", run_kind=None, bpm=0) -> dict:
    """Pick the next song. reason: start | skip (the current song is skipped: never again) | next (moved on: neutral) |
    phase (the workout moved on). source is an optional hint: artist | surprise | speech | cadence.
    -> {"ok", "message", "song", "upnext", "fallback_link"?}; song and upnext carry "link" for his service.
    trainer._lock is held only to read and to record: never while a search runs (at most TOTAL_S seconds)."""
    reason = reason if reason in ("start", "skip", "next", "phase") else "start"
    source = source if source in rules.HINTS else ""
    with trainer._lock:                                      # 1. read, and settle the fate of the song he had
        data = fitness._data()
        off = gate(data)
        if off:
            return _off(off)
        service = str(prefs(data).get("service") or "youtube_music")
        t0 = taste_now(data)
        day = trainer.view(data=data)
        st = _state()
        if not phase and reason in ("skip", "next") and st.get("phase") in rules.PHASES:
            phase = st["phase"]                              # a skip keeps the part of the workout the song was for
        ph = _phase(phase, day)
        ctx = _ctx(day, run_kind, bpm)
        if run_kind is None and not _bpm(bpm) and isinstance(st.get("ctx"), dict) and reason in ("skip", "next"):
            ctx = {**ctx, **{k: v for k, v in st["ctx"].items() if k in ("run", "run_kind", "bpm")}}
        now = st.get("now") if isinstance(st.get("now"), dict) else None
        if now and reason in ("skip", "next"):
            if not _record("skip" if reason == "skip" else "neutral", now):
                return _off(_NOT_SAVED)
            st["now"] = None
            storage.save(STATE, st)
        t, events, learned = _with_learned(t0, _music_events())
        today = trainer._today().isoformat()
        hist = rules.history(events)
        up = st.get("upnext") if isinstance(st.get("upnext"), dict) else None
        song = None
        if reason in ("skip", "next") and not source and up and up.get("phase") == ph and up.get("id") not in hist["skipped"] \
                and up.get("id") not in hist["recent"]:
            song = {k: v for k, v in up.items() if k != "link"}         # the one the page already opened inside the tap
        pool = _pool()
        want = [] if song else rules.needs(ph, t, events, pool, ctx, source, limit=4)
    if want:                                                 # 2. search with no lock held, capped in time
        pool.update(search_all(want))
    with trainer._lock:                                      # 3. choose and record
        t, events, learned = _with_learned(t0, _music_events())
        if song is None:
            song = _choose(ph, t, events, pool, ctx, source, today, "now")
        if song is not None:
            song = _learned_why(song, learned)
        if song is None:
            top = _top_query(ph, t, events, ctx, want)
            st = _state()
            st.update(now=None, upnext=None, at=trainer.clock().isoformat(timespec="seconds"), phase=ph,
                      fallback={"link": search_link(top), "label": f"Search: {top}"})
            storage.save(STATE, st)
            why = ("I could not reach YouTube search just now, so I have no pick." if time.monotonic() - _failed.get("search", -1e9) < FAIL_S
                   else "The search found nothing fit to play just now, so I have no pick.")
            return {"ok": False, "song": None, "upnext": None, "fallback_link": search_link(top), "fallback_label": f"Search: {top}",
                    "message": f"{why} The Captain America page has a search for \"{top}\" to open in your app instead."}
        if not _record("play", song, ph):
            return _off(_NOT_SAVED)
        after = events + [dict(song, type="music", act="play", date=today), dict(song, type="music", act="skip", date=today)]
        upnext = _choose(ph, t, after, pool, ctx, "", today, "upnext")
        upnext = _learned_why(upnext, learned) if upnext else None
        song = dict(song, link=link(song, service))
        upnext = dict(upnext, link=link(upnext, service)) if upnext else None
        st = _state()
        st.update(now=song, upnext=upnext, at=trainer.clock().isoformat(timespec="seconds"), phase=ph, ctx=ctx, liked=False, fallback=None)
        msg = _say(song)
        if service in ("spotify", "apple_music") and not st.get("told_search"):
            msg += f" {('Spotify' if service == 'spotify' else 'Apple Music')} cannot open a YouTube song, so Play opens a search for it there."
            st["told_search"] = True
        storage.save(STATE, st)
    return {"ok": True, "message": msg, "song": song, "upnext": upnext}


_NOT_SAVED = "I could not save that: the training log on this machine could not be read, so nothing was recorded."


def _is_pca(data: dict) -> bool:
    return trainer._pca(data)


def feedback(verdict: str, song_id: str = "", wait: bool = False) -> dict:
    """like (idempotent: a second like changes nothing) | unlike (takes a like back: the page's LIKE toggle) |
    skip (the next song, which the page already opened) | neutral.
    With Jarvis Link reachable: when the phone is playing a track from Metrolist's own queue (not Jarvis's pick), like /
    unlike / skip apply to THAT track (source "metrolist_queue"); a like is forwarded to Metrolist; a skip plays Jarvis's
    next pick by id. wait: voice (True) waits for the phone and says the true outcome; the page (False) does not."""
    verdict = str(verdict or "").strip().lower()
    if verdict in ("like", "unlike", "skip"):
        _keep_awake(MUSIC_ACTIVE)
        if not gate():
            q = _queue_target(song_id)
            if q is not None:
                return _rate_queue(verdict, q, wait)
    if verdict == "skip":
        return next_song(reason="skip", wait=wait)
    if verdict not in ("like", "unlike", "neutral"):
        return {"ok": False, "message": "Say like, skip or neutral."}
    out = _rate(verdict, song_id)
    if verdict == "like" and out.get("ok") and out.get("message", "").startswith("Liked"):
        out = _forward_like(out, wait, mine_only=True)
    return out


def _rate(verdict: str, song_id: str = "") -> dict:
    with trainer._lock:
        off = gate()
        if off:
            return _off(off)
        st = _state()
        now = st.get("now") if isinstance(st.get("now"), dict) else None
        if not now or (song_id and song_id != now.get("id")):
            return {"ok": False, "message": "There is no song of mine playing to rate."}
        if verdict == "like" and st.get("liked"):
            return {"ok": True, "message": f"You already liked {now['title']}."}
        if verdict == "unlike" and not st.get("liked"):
            return {"ok": True, "message": "That song was not liked."}
        if not _record(verdict, now):
            return {"ok": False, "message": _NOT_SAVED}
        if verdict == "neutral":
            return {"ok": True, "message": "Noted."}
        st["liked"] = verdict == "like"
        storage.save(STATE, st)
        return {"ok": True, "message": f"Liked. I'll play more like {now['title']}." if verdict == "like" else "Okay, not liked."}


def music_state() -> dict:
    """What the page shows: {"enabled", "service", "phase", "now", "upnext", "liked", "taste", "stats", "link_form", "picked_at",
    "fallback" (a search link {link, label} after a failed search, labelled as a search, never a pick)}. enabled is the
    same gate every action asks: the program runs and the DJ is on."""
    data = fitness._data()
    p = prefs(data)
    service = str(p.get("service") or "none")
    st = _state()
    events = _music_events()
    day = trainer.view(data=data) if _is_pca(data) else None
    t = taste_now(data)
    return {"enabled": not gate(data), "service": service, "phase": _phase("", day), "now": st.get("now"), "upnext": st.get("upnext"),
            "liked": bool(st.get("liked")), "picked_at": st.get("at"), "fallback": st.get("fallback"),
            "taste": {k: t[k] for k in ("artists", "genres", "avoid", "open", "cadence", "tempo")},
            "learned": learned_artists(events, t),
            "stats": {"plays": sum(e["act"] == "play" for e in events), "likes": sum(e["act"] == "like" for e in events),
                      "skips": sum(e["act"] == "skip" for e in events)},
            "link_form": LINK_FORM, "link": link_view(data, st)}


def picked_within(seconds: float) -> bool:
    st = _state()
    try:
        at = datetime.fromisoformat(str(st.get("at")))
    except (TypeError, ValueError):
        return False
    return bool(st.get("now")) and 0 <= (trainer.clock() - at).total_seconds() <= seconds


def set_taste(body: dict) -> str:
    """Save his edited lists (artists, genres, avoid: short strings) and switches (open, cadence, tempo)."""
    body = body if isinstance(body, dict) else {}
    clean: dict = {}
    for k in ("artists", "genres", "avoid"):
        if k in body:
            v = body[k]
            if not isinstance(v, list) or len(v) > 30 or any(not isinstance(x, str) or not x.strip() or len(x) > 40 for x in v):
                return f"Send {k} as a list of short names (up to 30, 40 letters each)."
            clean[k] = rules._str_list(v)
    for k in ("open", "cadence", "tempo"):
        if k in body:
            if not isinstance(body[k], bool):
                return f"Send {k} as true or false."
            clean[k] = body[k]
    if not clean:
        return "Nothing to change in your music taste."
    with trainer._lock:
        data = fitness._data()
        off = gate(data)
        if off:
            return off
        st = _pca_state(data)
        st["music"] = {**(st.get("music") or {}), **clean}
        fitness._save(data)
        ms = _state()
        now = ms.get("now") if isinstance(ms.get("now"), dict) else None
        if now:                                          # the up-next song follows the new taste (from the cache: no wait)
            t, events, learned = _with_learned(taste_now(data), _music_events())
            today = trainer._today().isoformat()
            after = events + [dict(now, type="music", act="skip", date=today)]
            up = _choose(ms.get("phase") or "warmup", t, after, _pool(), ms.get("ctx") or {}, "", today, "upnext")
            up = _learned_why(up, learned) if up else None
            service = str(prefs(data).get("service") or "youtube_music")
            ms["upnext"] = dict(up, link=link(up, service)) if up else None
            storage.save(STATE, ms)
    return "Music taste saved."


# ---- Jarvis Link: Jarvis runs the music on his phone (tools/pca_link, android/jarvis-link) ----------------------------
# The rules above stay as they are. When the picker is on AND the phone's Jarvis Link app checked in within 90 s
# (pca_link.reachable), a pick is also PLAYED on the phone by its YouTube id (play_id); voice waits for the phone and says
# the true outcome, the page never waits (it polls music_state, whose "link" block reports the outcome). On a failure or
# no answer the page shows the PLAY tap link exactly as before ("via": "tap"). Nothing here stops music on its own,
# except the opt-in "stop music when the workout ends".
#
# Metrolist's own queue: after Jarvis's song, Metrolist plays similar songs itself. playing() tells the two apart by the
# video id inside the session's media id ("auto:queue:<key>:" + base64url(id), or a bare id; android/.../Metro.java).
# Likes and skips of a queue track are music events with source "metrolist_queue" and the artist the phone reported;
# an artist he likes there twice (net of skips and unlikes) is LEARNED: a low-weight artist source for the picker
# (a learned artist starts at 1/1.3^4 = 0.35 of a listed one, applied as in-memory priors so core/pca_music is unchanged).
#
# Auto-DJ, tick(): called by the server whenever the phone talks to it (core/appserver: every /api/link/state report,
# sent on each track change and every 30 s while playing, and every /api/link/cmd poll, at most every 45 s while idle and
# continuously while a workout keeps the Link active). Rules, each logged as a music event {"act": "auto", "what",
# "reason"}: (a) a workout session is open and its phase changed (warm-up, lift, easy/hard run, cool-down) -> a pick for
# the new phase; (b) the phone moved on to a Metrolist-queue track during a session -> kept when acceptable for the phase
# (core/pca_music.acceptable by title/artist, unknown length allowed; no speech on a hard run or in the cool-down, no hype
# in the cool-down, nothing he skipped or avoids), else replaced by a pick; (c) the session ends -> nothing, unless he set
# "stop_at_end" (default off); (d) never anything without an open session, never two automatic changes within 20 s;
# (e) audio prefs "auto_dj" (default ON once the Link is paired) turns (a) and (b) off.
import base64                                               # noqa: E402

LINK_WAIT = 12            # s: a voice/skill play waits this long for the phone
PENDING_S = 13            # s: after this the page says the phone did not confirm, and shows the tap link
CMD_WAIT = 8              # s: a voice pause/resume/like waits this long
AUTO_GAP = 20             # s: at most one automatic change per 20 s
PAGE_ACTIVE = 600         # s: the page open with the picker on keeps the phone polling
MUSIC_ACTIVE = 600        # s: any music command does too
SESSION_TAIL = 300        # s: an open workout keeps it polling until 5 min after its last event
FRESH_S = 120             # s: an older now-playing report is not "what is playing"
LEARN_LIKES = 2
LEARNED_PRIOR = 4
_VID = re.compile(r"[A-Za-z0-9_-]{11}")
_HYPE = re.compile(r"\b(?:hype|gym|workout|pump(?:ed)?|beast|aggressive|phonk|rage|bpm|motivation(?:al)?|turn up)\b", re.I)
_HYPE_GENRES = ("drill", "edm", "metal", "phonk")
_SPEECHY = re.compile(r"\b(?:speech|motivational|motivation video)\b", re.I)
_SESSION_EVENTS = ("start", "set", "swap", "skip", "rep", "run", "test")
_NOT_PAIRED = ("Jarvis Link is not set up on your phone, so I can't control Metrolist from here. The audio settings on the "
               "Captain America page show how.")
_UNREACHABLE = "I can't reach your phone right now: Jarvis Link has not checked in for a while."


def _link():
    from tools import pca_link
    return pca_link


def _keep_awake(seconds: float) -> None:
    """Keep the phone polling (only for a person who paired the Link: nobody else gets a queue file)."""
    try:
        pl = _link()
        if pl.paired():
            pl.keep(seconds)
    except Exception:                                        # noqa: BLE001 - never costs a music call
        pass


def auto_dj(p: dict, paired: bool) -> bool:
    """The auto-DJ switch: on by default once the Link is paired; prefs auto_dj false turns it off."""
    return p.get("auto_dj", paired) not in (False, None, "", "false", "off", "no", 0)


def vid_of(media_id) -> str:
    """The YouTube id inside Metrolist's session media id ("auto:queue:<key>:" + base64url(id), "auto:song:..." or a
    bare id); '' when there is none."""
    m = str(media_id or "")
    if _VID.fullmatch(m):
        return m
    tail = m.rsplit(":", 1)[-1]
    if not tail or not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", tail):
        return ""
    try:
        raw = base64.urlsafe_b64decode(tail + "=" * (-len(tail) % 4)).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return ""
    return raw if _VID.fullmatch(raw) else ""


def _first_artist(artist) -> str:
    return re.split(r",|&| feat\.? | ft\.? | x | and ", str(artist or ""), maxsplit=1, flags=re.I)[0].strip()[:40]


def playing(st: dict | None = None) -> dict | None:
    """What the phone says is playing now (fresh within FRESH_S, Metrolist has a session and a title), and whether it is
    Jarvis's pick ("mine") or a track from Metrolist's own queue. None when the phone has not said."""
    pl = _link()
    s = pl.state()
    if not isinstance(s, dict) or s.get("age_s", 1e9) > FRESH_S or s.get("session") is False or not s.get("title"):
        return None
    st = st if st is not None else _state()
    now = st.get("now") if isinstance(st.get("now"), dict) else None
    vid = vid_of(s.get("media_id"))
    mine = False
    if now:
        if vid:
            mine = vid == now.get("id")
        else:
            got, want = rules.norm_title(s["title"]), rules.norm_title(now.get("title"))
            mine = bool(got) and got in want
    sig = vid or f"{rules.norm_title(s['title'])}|{str(s.get('artist') or '').lower()}"
    return {"title": s["title"], "artist": s.get("artist") or "", "playing": bool(s.get("playing")), "liked": s.get("liked"),
            "position_ms": s.get("position_ms"), "duration_ms": s.get("duration_ms"), "age_s": s.get("age_s"),
            "id": vid or None, "mine": mine, "source": "jarvis" if mine else "metrolist_queue", "sig": sig}


def link_reachable() -> bool:
    """Is the Jarvis Link app paired for this person AND did it check in within 90 s? Voice routes "pause the music",
    "resume the music" and "what song is this" to Jarvis only then (core/coachintent); otherwise the normal path."""
    try:
        pl = _link()
        return pl.paired() and pl.reachable()
    except Exception:                                        # noqa: BLE001
        return False


def _queue_target(song_id: str = "") -> dict | None:
    """The Metrolist-queue track a like/skip applies to, or None (then it is Jarvis's own song, as before)."""
    try:
        pl = _link()
        if not pl.reachable():
            return None
        st = _state()
        now = st.get("now") if isinstance(st.get("now"), dict) else None
        if song_id and now and song_id == now.get("id"):
            return None
        p = playing(st)
    except Exception:                                        # noqa: BLE001
        return None
    return p if p and not p["mine"] else None


# ---- learned artists ---------------------------------------------------------------------------------------------------
def learned_artists(events: list, t: dict) -> list[str]:
    """Artists he liked in Metrolist's queue at least LEARN_LIKES times, net of his skips and unlikes there, that he did
    not list and does not avoid."""
    score: dict[str, list] = {}
    for e in events or []:
        if e.get("source") != "metrolist_queue" or not e.get("artist"):
            continue
        name = rules.known(_first_artist(e["artist"]))
        if not name:
            continue
        row = score.setdefault(name.lower(), [name, 0])
        row[1] += {"like": 1, "unlike": -1, "skip": -1}.get(e.get("act"), 0)
    listed = {a.lower() for a in t.get("artists") or []}
    avoid = [str(a).lower() for a in t.get("avoid") or []]
    return [name for key, (name, n) in score.items() if n >= LEARN_LIKES and key not in listed and key not in avoid
            and not rules.avoided(name, t.get("avoid") or [])]


def _with_learned(t: dict, events: list) -> tuple[dict, list, list]:
    """His taste plus the learned artists as a LOW-weight artist source: each learned artist gets LEARNED_PRIOR in-memory
    'unlike' priors (never recorded), so the rules start it at 1/1.3^4 of a listed artist and his own likes move it."""
    learned = learned_artists(events, t)
    if not learned:
        return t, events, []
    prior = [{"type": "music", "act": "unlike", "artist": a, "id": None} for a in learned for _ in range(LEARNED_PRIOR)]
    return dict(t, artists=list(t.get("artists") or []) + learned), prior + list(events), learned


def _learned_why(song: dict, learned: list) -> dict:
    if song and song.get("source") == "artist" and song.get("artist") in learned:
        return dict(song, why=f"Because you liked {song['artist']} in Metrolist.")
    return song


# ---- playing a pick on the phone ---------------------------------------------------------------------------------------
# The Link and auto-DJ bookkeeping lives in fitness/music-auto-<machine>.json, written only by that machine (the PC's voice
# commands, the laptop's ticks), never in the shared music-state.json: Syncthing makes conflict copies of a file two
# machines write. Reads merge both machines' files (the newest command wins); the auto-DJ reads only its own machine's.
def _machine() -> str:
    return _link().machine()                                 # the same machine name as tools/pca_link's files


def _book_name(m: str) -> str:
    return f"fitness/music-auto-{m}.json"


def _book(m: str | None = None) -> dict:
    b = storage.load(_book_name(m or _machine()), {})
    return b if isinstance(b, dict) else {}


def _links() -> dict:
    """The newest "play" and "last" command rows across both machines' files."""
    out: dict = {}
    for m in ("pc", "server"):
        lk = _book(m).get("link")
        if not isinstance(lk, dict):
            continue
        for key in ("play", "last"):
            row = lk.get(key)
            newer = isinstance(row, dict) and isinstance(row.get("t"), (int, float)) and (key not in out or row["t"] >= out[key]["t"])
            if newer:
                out[key] = row
    return out


def _note_cmd(what: str, cid: str, song_id: str = "") -> None:
    with trainer._lock:
        b = _book()
        lk = b.get("link") if isinstance(b.get("link"), dict) else {}
        row = {"id": cid, "what": what, "t": _link().clock(), "song_id": song_id}
        lk["last"] = row
        if what == "play":
            lk["play"] = row
        b["link"] = lk
        storage.save(_book_name(_machine()), b)


def _outcome(r: dict | None) -> dict:
    if r and r.get("ok"):
        return {"via": "link", "played": True, "link_message": "It's playing on your phone in Metrolist."}
    if r:
        detail = str(r.get("detail") or "").strip().rstrip(".")
        return {"via": "tap", "played": False, "link_message": "Your phone could not start it" + (f" ({detail[:120]})" if detail else "")
                + ". Tap Play on the Captain America page."}
    return {"via": "tap", "played": False,
            "link_message": f"Your phone did not confirm within {LINK_WAIT} seconds. Tap Play on the Captain America page."}


def _play_by_link(out: dict, wait: bool) -> dict:
    if not out.get("ok") or not out.get("song"):
        return out
    pl = _link()
    if not pl.reachable():
        return dict(out, via="tap") if pl.paired() else out
    song = out["song"]
    try:
        cid = pl.send("play_id", video_id=str(song.get("id") or ""), title=str(song.get("title") or ""),
                      artist=str(song.get("artist") or song.get("channel") or ""))
    except pl.LinkError:
        return dict(out, via="tap")
    _note_cmd("play", cid, song["id"])
    if not wait:
        return dict(out, via="link", pending=cid, played=None, message=out["message"] + " Sending it to your phone.")
    o = _outcome(pl.wait_result(cid, LINK_WAIT))             # no lock held: the phone answers through the server
    return dict(out, **o, message=f"{out['message']} {o['link_message']}")


def next_song(phase: str = "", reason: str = "start", source: str = "", run_kind=None, bpm=0, wait: bool = True) -> dict:
    """_pick (the rules: what plays and why), then, with Jarvis Link reachable, the same song played on his phone by id.
    wait=True (voice, skills): waits up to LINK_WAIT s and the message says the true outcome. wait=False (the page):
    returns at once with "via": "link", "pending": <command id>; music_state()["link"]["pending"] reports the outcome.
    -> adds "via": "link" | "tap" (and "played", "link_message") to _pick's answer when the Link is reachable."""
    _keep_awake(MUSIC_ACTIVE)
    return _play_by_link(_pick(phase, reason, source, run_kind, bpm), wait)


def play_now(wait: bool = False) -> dict:
    """PLAY on the page with the Link connected: Jarvis's current pick is sent to the phone again (nothing new is picked
    or recorded). Without a reachable Link the page keeps its tap link, so this only says so."""
    off = gate()
    if off:
        return _off(off)
    st = _state()
    now = st.get("now") if isinstance(st.get("now"), dict) else None
    if not now:
        return {"ok": False, "message": "There is no song of mine to play. Pick one first."}
    if not _link().reachable():
        return {"ok": False, "message": _UNREACHABLE + " Tap Play to open it in Metrolist."}
    _keep_awake(MUSIC_ACTIVE)
    out = _play_by_link({"ok": True, "message": f"{now['title']}.", "song": now, "upnext": st.get("upnext")}, wait)
    if out.get("pending") or out.get("link_message"):
        return out
    return dict(out, ok=False, message="That song cannot be sent to your phone. Tap Play to open it in Metrolist.")


def _send(name: str, wait: bool, timeout: float = CMD_WAIT, **args) -> tuple:
    """Queue one command when the phone is reachable -> (id or None, the result when waited for)."""
    pl = _link()
    if not pl.reachable():
        return None, None
    try:
        cid = pl.send(name, **args)
    except pl.LinkError:
        return None, None
    _note_cmd(name if name != "play" else "resume", cid)
    return cid, (pl.wait_result(cid, timeout) if wait else None)


def _forward_like(out: dict, wait: bool, mine_only: bool) -> dict:
    """A like is also sent to Metrolist (when the phone plays the song that was liked); the phone's answer is reported
    honestly, never assumed."""
    try:
        p = playing()
        if not _link().reachable() or p is None or (mine_only and not p["mine"]):
            return out
        cid, r = _send("like", wait)
    except Exception:                                        # noqa: BLE001 - the like is saved either way
        return out
    if cid is None:
        return out
    if not wait:
        return dict(out, pending=cid, message=out["message"] + " Liking it in Metrolist too.")
    if r and r.get("ok"):
        return dict(out, link_ok=True, message=out["message"] + " Liked in Metrolist too.")
    return dict(out, link_ok=False, message=out["message"] + " Your phone did not confirm the like in Metrolist.")


def _rate_queue(verdict: str, q: dict, wait: bool) -> dict:
    """Like / unlike / skip of the track Metrolist's own queue is playing: recorded as that track (source
    metrolist_queue, the artist the phone reported); a skip then plays Jarvis's next pick."""
    with trainer._lock:
        st = _state()
        ph = st.get("phase") if st.get("phase") in rules.PHASES else "warmup"
        liked = [x for x in st.get("queue_liked") or [] if isinstance(x, str)]
        if verdict == "like" and q["sig"] in liked:
            return {"ok": True, "message": f"You already liked {q['title']}."}
        if verdict == "unlike" and q["sig"] not in liked:
            return {"ok": True, "message": "That song was not liked."}
        dur = round(q["duration_ms"] / 1000) if q.get("duration_ms") else None
        ev = {"type": "music", "act": verdict, "id": q["id"] or "", "title": q["title"], "channel": q["artist"],
              "artist": _first_artist(q["artist"]), "duration_s": dur, "source": "metrolist_queue", "query": "", "phase": ph}
        if trainer.record(ev).get("unsaved"):
            return {"ok": False, "message": _NOT_SAVED}
        if verdict == "like":
            st["queue_liked"] = (liked + [q["sig"]])[-50:]
        elif verdict == "unlike":
            st["queue_liked"] = [x for x in liked if x != q["sig"]]
        storage.save(STATE, st)
    who = f" by {q['artist']}" if q["artist"] else ""
    if verdict == "skip":
        out = next_song(reason="next", wait=wait)
        head = f"Skipped {q['title']}{who} from Metrolist's queue."
        return dict(out, message=f"{head} {out.get('message') or ''}".strip(), skipped_queue=q["title"])
    if verdict == "unlike":
        return {"ok": True, "message": "Okay, not liked here (Metrolist keeps its own like)."}
    out = {"ok": True, "message": f"Liked {q['title']}{who} from Metrolist's queue. I'll learn from it."}
    return _forward_like(out, wait, mine_only=False)


# ---- pause, resume, what is playing ------------------------------------------------------------------------------------
def control(what: str, wait: bool = False) -> dict:
    """pause | resume the music on his phone through Jarvis Link (never a tap link: there is nothing to tap)."""
    if what not in ("pause", "resume"):
        return {"ok": False, "message": "Say pause or resume."}
    off = gate()
    if off:
        return _off(off)
    pl = _link()
    if not pl.paired():
        return {"ok": False, "message": _NOT_PAIRED}
    _keep_awake(MUSIC_ACTIVE)
    if not pl.reachable():
        return {"ok": False, "message": _UNREACHABLE + (" Pause it in Metrolist." if what == "pause" else " Start it in Metrolist.")}
    cid, r = _send("pause" if what == "pause" else "play", wait)
    if cid is None:
        return {"ok": False, "message": "That command could not be queued for your phone."}
    if not wait:
        return {"ok": True, "pending": cid, "message": "Pausing the music." if what == "pause" else "Resuming the music."}
    if r and r.get("ok"):
        return {"ok": True, "message": "Paused." if what == "pause" else "Playing again."}
    if r:
        detail = str(r.get("detail") or "").strip().rstrip(".")
        return {"ok": False, "message": f"Your phone could not {what} the music" + (f" ({detail[:120]})." if detail else ".")}
    done = "paused" if what == "pause" else "resumed"
    return {"ok": False, "message": f"Your phone did not confirm within {CMD_WAIT} seconds, so I don't know if it {done}."}


def what_song() -> str:
    """What is playing on his phone, from Jarvis Link's report only (never a guess)."""
    pl = _link()
    if not pl.paired():
        return "I can't see your phone: Jarvis Link is not set up, so I don't know what is playing."
    _keep_awake(MUSIC_ACTIVE)
    p = playing() if pl.reachable() else None
    if p is None:
        return "I can't see your phone right now, so I don't know what is playing."
    by = f" by {p['artist']}" if p["artist"] else ""
    whose = " I picked it." if p["mine"] else " It's from Metrolist's own queue."
    if not p["playing"]:
        return f"Nothing is playing. Paused on {p['title']}{by}.{whose}"
    return f"This is {p['title']}{by}.{whose}"


def set_link_prefs(body: dict) -> str:
    """The NOW PLAYING card's switches: auto_dj (Jarvis manages the music during a workout) and stop_at_end."""
    body = body if isinstance(body, dict) else {}
    clean = {}
    for k in ("auto_dj", "stop_at_end"):
        if k in body:
            if not isinstance(body[k], bool):
                return f"Send {k} as true or false."
            clean[k] = body[k]
    if not clean:
        return "Nothing to change."
    with trainer._lock:
        data = fitness._data()
        off = gate(data)
        if off:
            return off
        st = _pca_state(data)
        st["audio"] = {**(st.get("audio") or {}), **clean}
        fitness._save(data)
    if "auto_dj" in clean:
        return "Auto is on: Jarvis runs the music during your workout." if clean["auto_dj"] else \
            "Auto is off: Jarvis changes the music only when you ask."
    return "Jarvis stops the music when the workout ends." if clean["stop_at_end"] else "Your music keeps playing when the workout ends."


def link_view(data: dict | None = None, st: dict | None = None) -> dict:
    """The "link" block of music_state: {paired, connected, last_contact, apk, auto_dj, stop_at_end, playing (playing() or
    None), pending (the last play sent: {id, what, song_id, waiting, done, ok, detail}), last (the last command of any
    kind, same shape)}."""
    pl = _link()
    data = data if data is not None else fitness._data()
    st = st if st is not None else _state()
    p = prefs(data)
    is_paired = pl.paired()
    out = {"paired": is_paired, "connected": False, "last_contact": None, "apk": pl.apk_path() is not None,
           "auto_dj": auto_dj(p, is_paired), "stop_at_end": p.get("stop_at_end") is True, "playing": None, "pending": None,
           "last": None}
    if not is_paired:
        return out
    out.update(connected=pl.reachable(), last_contact=pl.last_contact())
    out["playing"] = playing(st) if out["connected"] else None
    lk = _links()
    for key in ("play", "last"):
        row = lk.get(key)
        if not isinstance(row, dict) or not row.get("id"):
            continue
        try:
            age = pl.clock() - float(row.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= age < pl.RESULT_TTL:
            continue
        r = pl.result(str(row["id"]))
        waiting = r is None and age < PENDING_S
        detail = (r or {}).get("detail") or ("" if waiting or r else f"Your phone did not confirm within {LINK_WAIT} seconds.")
        out["pending" if key == "play" else "last"] = {"id": row["id"], "what": row.get("what"), "song_id": row.get("song_id") or "",
                                                       "waiting": waiting, "done": not waiting, "ok": bool(r and r.get("ok")),
                                                       "detail": str(detail)[:200]}
    return out


def page_touch(data: dict | None = None) -> None:
    """The page asked for its data: with the picker on, keep the phone polling for PAGE_ACTIVE s; with a workout open,
    until SESSION_TAIL s after its last event."""
    try:
        data = data if data is not None else fitness._data()
        if gate(data) or not _link().paired():
            return
        _link().keep(PAGE_ACTIVE)
        _keep_for_session(None, data)
    except Exception:                                        # noqa: BLE001 - never costs him the page
        pass


def _keep_for_session(rows: list | None, data: dict) -> bool:
    rows = rows if rows is not None else trainer.events()
    day = trainer.view(rows, data)
    if not day["session"]["open"]:
        return False
    today = trainer._today().isoformat()
    last = max((str(r["t"]) for r in rows if r.get("date") == today and r.get("type") in _SESSION_EVENTS), default="")
    try:
        left = (datetime.fromisoformat(last) + timedelta(seconds=SESSION_TAIL) - trainer.clock()).total_seconds()
    except ValueError:
        return False
    return left > 0 and _link().keep(left)


# ---- the auto-DJ -------------------------------------------------------------------------------------------------------
_SPEECH = re.compile(r"\b(?:motivational speech|speech|podcast|sermon)\b", re.I)


def queue_ok(p: dict, phase: str, t: dict, events: list | None = None) -> tuple[bool, str]:
    """Is a track Metrolist's queue started fit for this part of the workout? Deliberately NOT the search filter (a real
    song may be called "Hours", "Interview" or "Playlist"): a queue track is replaced only when (1) its title or artist
    shows a genre or artist he avoids, (2) he skipped that exact track, or that artist twice, (3) it is a speech, podcast
    or sermon during lifting or a hard run, or (4) in the cool-down, the artist's genre in the table is a hype genre AND
    the title carries a hype word. Its length never matters."""
    title, artist = str(p.get("title") or ""), str(p.get("artist") or "")
    words = f"{title} {artist}"
    bad = rules.avoided(words, t.get("avoid") or [])
    if bad:
        return False, f"avoided: {bad}"
    events = events if events is not None else _music_events()
    skips = [e for e in events if e.get("act") == "skip"]
    norm = rules.norm_title(title)
    if any((p.get("id") and e.get("id") == p["id"]) or (norm and rules.norm_title(e.get("title")) == norm) for e in skips):
        return False, "you skipped it before"
    who = rules.known(_first_artist(artist)).lower()
    if who and sum(rules.known(_first_artist(e.get("artist"))).lower() == who for e in skips) >= 2:
        return False, f"you skipped {rules.known(_first_artist(artist))} twice"
    if phase in ("hard", "lift") and _SPEECH.search(words):
        return False, f"a speech during the {rules.PHASE_WORDS.get(phase, phase)}"
    if phase == "cooldown" and _HYPE.search(title) and any(g in _HYPE_GENRES for g in rules.genres_of([_first_artist(artist)])):
        return False, "hype music during the cool-down"
    return True, "fits the " + rules.PHASE_WORDS.get(phase, phase)


def _auto_event(what: str, reason: str, phase: str, item: dict | None = None) -> None:
    item = item or {}
    trainer.record({"type": "music", "act": "auto", "what": what, "reason": reason[:200], "phase": phase,
                    "title": str(item.get("title") or "")[:200], "artist": str(item.get("artist") or item.get("channel") or "")[:120],
                    "id": item.get("id") or ""})


def _save_auto(auto: dict) -> None:
    with trainer._lock:
        b = _book()
        b["auto"] = auto
        storage.save(_book_name(_machine()), b)


STOP_WINDOW = 120         # s: "stop at the end" acts only this soon after the workout ended
BACKOFF_S = 300           # s: after a failed automatic pick, no new automatic attempt for this long (or a pick succeeds)
AUTO_MAX = 60             # automatic events (keep / change / stop) per workout session at most


def _ended_at(rows: list) -> datetime | None:
    today = trainer._today().isoformat()
    last = max((str(r["t"]) for r in rows if r.get("type") == "end" and r.get("date") == today), default="")
    try:
        return datetime.fromisoformat(last) if last else None
    except ValueError:
        return None


def _seen_track(auto: dict, sig) -> None:
    if sig and sig not in (auto.get("tracks") or []):
        auto["tracks"] = ((auto.get("tracks") or []) + [sig])[-40:]


def _event(auto: dict, what: str, reason: str, phase: str, item: dict | None = None) -> None:
    auto["events"] = int(auto.get("events") or 0) + 1
    _auto_event(what, reason, phase, item)


def _backing_off(auto: dict, st: dict) -> bool:
    """A failed automatic pick less than BACKOFF_S ago, and no successful pick (by anyone) since."""
    try:
        failed = datetime.fromisoformat(str(auto.get("failed_at")))
    except (TypeError, ValueError):
        return False
    if (trainer.clock() - failed).total_seconds() >= BACKOFF_S:
        return False
    try:
        return not (st.get("now") and datetime.fromisoformat(str(st.get("at"))) > failed)
    except (TypeError, ValueError):
        return True


def _stop_at_end(auto: dict, sess: dict, rows: list, p: dict, st: dict, now: float) -> dict | None:
    """(c): pause the music once, right after the workout ended, only when Auto and "stop at the end" are both on and
    the phone plays music this session's auto-DJ played or saw (never his own music started afterwards)."""
    if not (sess.get("ended") and auto_dj(p, True) and p.get("stop_at_end") is True):
        return None
    if auto.get("stopped_for") == auto.get("session"):
        return None
    ended = _ended_at(rows)
    if ended is None or not 0 <= (trainer.clock() - ended).total_seconds() <= STOP_WINDOW:
        return None
    pl_now = playing(st)
    if not (pl_now and pl_now["playing"] and (pl_now["mine"] or pl_now["sig"] in (auto.get("tracks") or []))):
        return None
    cid, _ = _send("pause", False)
    if not cid:
        return None
    auto.update(stopped_for=auto.get("session"), changed_at=now)
    _event(auto, "stop", "The workout ended and you asked me to stop the music then.", "cooldown", pl_now)
    return {"action": "stop", "reason": "The workout ended and stop_at_end is on."}


def tick(now: float | None = None) -> dict:
    """One auto-DJ decision for the person acting -> {"action": none|wait|keep|phase|replace|stop, "reason", ...}.
    Deterministic; every action that changes, keeps or stops music is logged as a music event with its reason."""
    pl = _link()
    now = pl.clock() if now is None else now
    data = fitness._data()
    if gate(data) or not pl.paired():
        return {"action": "none", "reason": "the picker is off or Jarvis Link is not paired"}
    rows = trainer.events()
    day = trainer.view(rows, data)
    sess = day.get("session") or {}
    st = _state()
    b = _book()
    auto = dict(b["auto"]) if isinstance(b.get("auto"), dict) else {}
    p = prefs(data)
    if not sess.get("open"):                                  # (c), (d)
        if not auto.get("open"):
            return {"action": "none", "reason": "no workout is open"}
        auto["open"] = False
        out = _stop_at_end(auto, sess, rows, p, st, now)
        _save_auto(auto)
        return out or {"action": "none", "reason": "no workout is open"}
    _keep_for_session(rows, data)
    started = str(sess.get("started") or "")
    if auto.get("session") != started and auto.get("tracks_for") != started:
        auto.update(tracks=[], tracks_for=started, events=0, failed_at=None)       # a new session: fresh bookkeeping
    if not auto_dj(p, True):                                  # (e)
        if not auto.get("open"):
            _save_auto(dict(auto, open=True))
        return {"action": "none", "reason": "auto is off"}
    if not pl.reachable():
        return {"action": "none", "reason": "the phone is not reachable"}
    from tools import pca
    phase = pca.audio_phase(day)
    if phase not in rules.PHASES:
        return {"action": "none", "reason": f"no music phase ({phase})"}
    new_session = auto.get("session") != started
    pl_now = playing(st)
    before = list(auto.get("tracks") or [])
    if pl_now and (pl_now["mine"] or not new_session):
        _seen_track(auto, pl_now["sig"])
    t = taste_now(data)
    mine_fits = bool(pl_now and pl_now["mine"] and (st.get("now") or {}).get("phase") == phase)
    decision, reason = None, ""
    if new_session or auto.get("phase") != phase:             # (a)
        if new_session and pl_now and pl_now["playing"] and (mine_fits or not pl_now["mine"] and queue_ok(pl_now, phase, t)[0]):
            decision, reason = "keep", f"The workout started and what is playing already fits the {rules.PHASE_WORDS[phase]}."
        elif not new_session and mine_fits:
            decision, reason = "keep", f"My song for the {rules.PHASE_WORDS[phase]} is already playing."
        elif new_session:
            decision, reason = "phase", f"The workout started: music for the {rules.PHASE_WORDS[phase]}."
        else:
            decision = "phase"
            reason = f"The workout moved from the {rules.PHASE_WORDS.get(auto.get('phase'), 'last part')} to the {rules.PHASE_WORDS[phase]}."
    elif pl_now and pl_now["playing"] and not pl_now["mine"] and pl_now["sig"] != auto.get("seen"):     # (b)
        ok, why = queue_ok(pl_now, phase, t)
        decision = "keep" if ok else "replace"
        reason = (f"Metrolist's queue moved on to {pl_now['title']}: it {why}, so it stays." if ok
                  else f"Metrolist's queue moved on to {pl_now['title']}: {why}, so I replaced it.")
    if decision is None or int(auto.get("events") or 0) >= AUTO_MAX:
        if not auto.get("open") or auto.get("tracks") != before:
            _save_auto(dict(auto, open=True))
        return {"action": "none", "reason": "nothing changed" if decision is None else f"{AUTO_MAX} automatic changes this session"}
    if decision in ("phase", "replace"):
        if now - float(auto.get("changed_at") or 0) < AUTO_GAP:                                      # (d)
            return {"action": "wait", "reason": f"the last automatic change was under {AUTO_GAP} seconds ago"}
        if _backing_off(auto, st):
            return {"action": "wait", "reason": "the last automatic pick failed under " + plural(BACKOFF_S // 60, "minute") + " ago"}
    auto.update(open=True, session=started, phase=phase)
    if decision == "keep":
        auto["seen"] = pl_now["sig"] if pl_now else auto.get("seen")
        _event(auto, "keep", reason, phase, pl_now)
        _save_auto(auto)
        return {"action": "keep", "reason": reason}
    auto["changed_at"] = now
    if decision == "replace":
        auto["seen"] = pl_now["sig"]                          # judged once, whatever the pick does
    _save_auto(auto)                                          # first: a slow search never lets a second tick act too
    out = next_song(phase=phase, reason="phase" if decision == "phase" else "next", wait=False)
    if out.get("ok") and out.get("song"):
        auto.update(seen=out["song"]["id"], failed_at=None)
        _seen_track(auto, out["song"]["id"])
        _event(auto, decision, reason, phase, out["song"])
    else:
        auto["failed_at"] = trainer.clock().isoformat(timespec="seconds")
        _event(auto, decision + "_failed", f"{reason} No pick: {out.get('message') or 'the search failed'}", phase)
    _save_auto(auto)
    return {"action": decision, "reason": reason, "song": out.get("song"), "via": out.get("via"), "pending": out.get("pending")}


_tick_busy = threading.Lock()
_tick_last: dict = {}
TICK_EVERY = 5            # s: the phone's requests run the auto-DJ at most this often per person


def tick_soon(uid: str) -> bool:
    """Run tick() for this person in a background thread, one at a time and at most every TICK_EVERY s: the phone's
    requests (core/appserver /api/link/state and /api/link/cmd) call this and never wait for it."""
    if time.monotonic() - _tick_last.get(uid, -1e9) < TICK_EVERY or not _tick_busy.acquire(blocking=False):
        return False
    _tick_last[uid] = time.monotonic()

    def run():
        from core import users
        try:
            with users.acting_as(uid):
                tick()
        except Exception as e:                               # noqa: BLE001 - the auto-DJ never breaks the phone's poll
            try:
                from core import diag
                diag.swallowed("pca auto-dj", e, every=600)
            except Exception:                                # noqa: BLE001
                pass
        finally:
            _tick_busy.release()
    threading.Thread(target=run, daemon=True, name="pca-autodj").start()
    return True
