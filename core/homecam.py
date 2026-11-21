"""House cameras: live view and recordings from the home lab, for the owner only.

The cameras record on the NLfam Pi (integration/homecam: go2rtc + recorder + the `camgate` gateway on port 8099). Jarvis
only reads through camgate, which allows the Jarvis PC and laptop and nobody else, and every Jarvis route that shows a
camera is owner-only. Nothing here talks to a vendor cloud or a paid API.

Camera ids come from the Pi's status (one go2rtc.yaml entry per camera), so camera #2 shows up here by itself:
"living_room" is said "living room".
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import weakref

def gate() -> str:
    """camgate on the Pi (read each call, so tests and a changed .env take effect without a restart)."""
    return os.getenv("HOMECAM_URL", f"http://{os.getenv('HOMELAB_HOST', '192.168.1.50')}:8099").rstrip("/")
NAME = re.compile(r"^[a-z0-9_]{1,32}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CAMERA_ROLES = {"owner", "wife", "husband", "spouse", "partner"}   # who may watch the house cameras and gets their alerts


def camera_users() -> list[str]:
    """Everyone who watches the cameras: the owner and his spouse (owner, 2026-11-20). Their alerts go to each of them."""
    from core import users
    try:
        found = [uid for uid, u in users.all_users().items() if (u or {}).get("role") in CAMERA_ROLES]
    except Exception:  # noqa: BLE001 - a broken user file must never cost the owner his alert
        found = []
    return found or ["owner"]


SEGMENT = re.compile(r"^\d{2}-\d{2}-\d{2}(?:-card-\d{1,4})?\.mp4$")   # "-card-<s>": a gap filled from the camera's SD card
HIDDEN = re.compile(r"^(?:test|_)")          # the test pattern used to prove the recorder is not a room of the house
LIVE_MAX_SECONDS = 30 * 60                    # one live view is cut after 30 minutes (a forgotten tab mustn't stream all night)
LIVE_MAX_VIEWERS = 6
LIVE_IDLE_SECONDS = 5.0                       # a viewer that pulled nothing for this long has gone: its place is freed
_live = threading.BoundedSemaphore(LIVE_MAX_VIEWERS)
_streams = weakref.WeakSet()                  # weak: a view nobody holds any more still closes itself (__del__)
_streams_lock = threading.Lock()
_cache: dict = {"at": 0.0, "status": None}


class CameraError(Exception):
    """Something between Jarvis and the cameras is wrong, said in words he can act on."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def _open(path: str, timeout: float = 8.0, headers: dict | None = None):
    try:
        return urllib.request.urlopen(urllib.request.Request(gate() + path, headers=headers or {}), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise CameraError("The camera gateway on the Pi refused this computer. Its address probably changed: "
                              "add it to ~/homecam/allow.conf on the Pi.", 502) from None
        if e.code in (404, 500, 502, 503, 504):
            raise CameraError("That camera isn't sending video right now.", 404 if e.code == 404 else 503) from None
        raise CameraError(f"The camera gateway answered {e.code}.", 502) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CameraError("I can't reach the camera recorder on the Pi.", 503) from None
    except ValueError:                                  # http.client.InvalidURL: a name that slipped past the checks
        raise CameraError("No such camera.", 404) from None


def _check(cam: str) -> str:
    cam = str(cam or "").strip().lower()
    if not NAME.fullmatch(cam):
        raise CameraError("No such camera.", 404)
    return cam


def label(cam: str) -> str:
    return cam.replace("_", " ").strip().capitalize()


def status(fresh: bool = False) -> dict:
    """The recorder's own report (/recordings/_status.json on the Pi), cached 10 s."""
    now = time.monotonic()
    if not fresh and _cache["status"] is not None and now - _cache["at"] < 10:
        return _cache["status"]
    with _open("/status.json", timeout=3) as r:
        data = json.loads(r.read().decode("utf-8"))
    _cache.update(at=now, status=data)
    return data


def cameras(fresh: bool = False) -> list[dict]:
    """Every house camera with what it's doing. The recorder's status file is up to date within 5 s; older than a
    minute means the recorder itself is down, and no camera is called recording then."""
    data = status(fresh)
    stale = time.time() - float(data.get("updated") or 0) > 60
    out = []
    for cam, s in (data.get("cameras") or {}).items():
        if not NAME.fullmatch(cam) or (HIDDEN.match(cam) and not os.getenv("HOMECAM_SHOW_TEST")):
            continue
        error = str(s.get("error") or "")
        if re.search(r"404 Not Found|Connection refused|No route to host|timed out", error, re.I):
            error = "The recorder can't get video from the camera at its network address."
        out.append({"id": cam, "label": label(cam), "privacy": bool(s.get("privacy")),
                    "recording": bool(s.get("recording")) and not stale,
                    "last_video_at": s.get("last_video_at"), "error": "" if s.get("recording") else error[:200],
                    "segments": s.get("segments", 0), "gb_per_day": s.get("gb_per_day_measured")})
    return out


def summary(fresh: bool = False) -> dict:
    data = status(fresh)
    return {"cameras": cameras(fresh), "retention_days": data.get("retention_days"), "disk": data.get("disk") or {},
            "recorder_up": time.time() - float(data.get("updated") or 0) <= 60}


def frame(cam: str) -> bytes:
    """One current JPEG from the camera (go2rtc makes it from the live stream)."""
    cam = _check(cam)
    _not_private(cam)
    with _open(f"/live/frame.jpeg?src={cam}", timeout=12) as r:
        data = r.read(5_000_000)
    if not data.startswith(b"\xff\xd8"):
        raise CameraError("That camera isn't sending video right now.", 503)
    return data


class LiveStream:
    """The live stream as fragmented MP4 chunks. It holds one of LIVE_MAX_VIEWERS places and the connection to the Pi;
    close() gives both back exactly once, and runs from the end of iteration, the response's background task, and
    garbage collection, so a viewer who leaves before the first byte doesn't keep a place forever."""

    def __init__(self, cam: str, kind: str = "mp4"):
        _reap_idle()
        if not _live.acquire(blocking=False):
            raise CameraError("Too many live views are open. Close one and try again.", 429)
        self._lock, self._closed, self.resp = threading.Lock(), False, None
        self.ctype, self.last = "video/mp4", time.monotonic()
        with _streams_lock:
            _streams.add(self)
        try:
            if kind in ("mjpeg", "mjpeg_hd"):           # motion-JPEG made by go2rtc: iPhones get the small stream; the
                src = f"{cam}_rt" if kind == "mjpeg_hd" else f"{cam}_sub"   # steady view the 1280x720 one (<cam>_rt)
                self.resp = _open(f"/live/stream.mjpeg?src={src}", timeout=15)
                self.ctype = self.resp.headers.get("Content-Type") or "multipart/x-mixed-replace; boundary=frame"
            elif kind == "video":                       # the instant player: picture only, never waits for sound
                self.resp = _open(f"/live/stream-video.mp4?src={cam}", timeout=15)
            else:
                self.resp = _open(f"/live/stream.mp4?src={cam}", timeout=15)
        except BaseException:
            self.close()
            raise

    def __iter__(self):
        started = time.monotonic()
        try:
            # read1: pass on whatever has arrived. read(64 KB) waited until 64 KB were in, so a 39 KB/s camera reached
            # the phone in 1-2 s clumps: play, freeze, play (measured 2026-11-18, max gap 2.0 s).
            read = getattr(self.resp, "read1", None) or self.resp.read
            while not self._closed and time.monotonic() - started < LIVE_MAX_SECONDS:
                block = read(64 * 1024)
                if not block:
                    break
                self.last = time.monotonic()
                yield block
        except (OSError, ValueError, AttributeError):   # closed under us: the viewer left
            return
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        with _streams_lock:
            _streams.discard(self)
        try:
            if self.resp is not None:
                self.resp.close()
        finally:
            _live.release()

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


def _reap_idle(now: float | None = None) -> int:
    """Free the places of viewers who left: the web server doesn't always close a stream whose phone went away
    (2026-11-18: his own earlier views filled all places and the next one was refused)."""
    now = now or time.monotonic()
    with _streams_lock:
        idle = [s for s in _streams if now - s.last > LIVE_IDLE_SECONDS]
    for s in idle:
        s.close()
    return len(idle)


def live(cam: str, video_only: bool = False) -> LiveStream:
    """The live stream (H.264 as the camera sends it, no re-encoding). At most LIVE_MAX_VIEWERS at once, LIVE_MAX_SECONDS each.
    video_only: the picture without the sound track (the instant player), which never waits for the camera's mic."""
    cam = _check(cam)
    _not_private(cam)
    return LiveStream(cam, "video" if video_only else "mp4")


def webrtc_answer(cam: str, offer: str) -> str:
    """Near-instant live view: hand the browser's WebRTC offer to go2rtc and return its answer. On the PC that is the
    Pi's go2rtc (through camgate); on the phone server HOMECAM_WEBRTC_URL points at the laptop's own relay, which
    phones reach over Tailscale. The video itself never touches the public internet."""
    cam = _check(cam)
    _not_private(cam)
    offer = str(offer or "")
    if not offer.startswith("v=0") or len(offer) > 60000:
        raise CameraError("That isn't a video-call offer.", 400)
    base = os.getenv("HOMECAM_WEBRTC_URL", "").strip().rstrip("/")
    url = f"{base}?src={cam}" if base else f"{gate()}/webrtc?src={cam}"
    req = urllib.request.Request(url, data=offer.encode("utf-8"), method="POST", headers={"Content-Type": "application/sdp"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            answer = r.read(200000).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CameraError("The instant-view relay isn't answering.", 503) from None
    if not answer.startswith("v=0"):
        raise CameraError("The instant-view relay gave no answer.", 502)
    return answer


def live_mjpeg(cam: str, hd: bool = False) -> LiveStream:
    """Live view for browsers that can't play the MP4 stream (iPhone): ~16 fps motion-JPEG at 640x360, no sound."""
    cam = _check(cam)
    _not_private(cam)
    return LiveStream(cam, "mjpeg_hd" if hd else "mjpeg")


def _listing(path: str) -> list[dict]:
    with _open(path, timeout=8) as r:
        return json.loads(r.read().decode("utf-8") or "[]")


def recordings(cam: str, limit: int = 60) -> list[dict]:
    """The newest recorded segments of one camera, newest first: {day, file, start, size, url}."""
    cam = _check(cam)
    limit = max(1, min(int(limit), 2000))
    try:
        days = sorted((d["name"] for d in _listing(f"/recordings/{cam}/")
                       if d.get("type") == "directory" and DAY.fullmatch(str(d.get("name")))), reverse=True)
    except CameraError as e:
        if e.status == 404:
            return []
        raise
    out: list[dict] = []
    for day in days:
        files = sorted((f for f in _listing(f"/recordings/{cam}/{day}/")
                        if f.get("type") == "file" and SEGMENT.fullmatch(str(f.get("name")))), key=lambda f: f["name"], reverse=True)
        for f in files:
            item = {"day": day, "file": f["name"], "start": f"{day}T{f['name'][:8].replace('-', ':')}",
                    "size": int(f.get("size") or 0), "url": f"/api/house/recording/{cam}/{day}/{f['name']}"}
            if "-card-" in f["name"]:                  # from the camera's own SD card: say so, and how long it is
                item.update(card=True, seconds=seg_len(f["name"]))
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def recording(cam: str, day: str, file: str, range_header: str = ""):
    """One recorded segment, honouring a Range request so the player can seek: (status, headers, chunk iterator)."""
    cam = _check(cam)
    if not DAY.fullmatch(day or "") or not SEGMENT.fullmatch(file or ""):
        raise CameraError("No such recording.", 404)
    headers = {"Range": range_header} if re.fullmatch(r"bytes=\d*-\d*", range_header or "") else {}
    resp = _open(f"/recordings/{cam}/{day}/{file}", timeout=15, headers=headers)
    out = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for h in ("Content-Length", "Content-Range"):
        if resp.headers.get(h):
            out[h] = resp.headers[h]

    def chunks():
        try:
            while True:
                block = resp.read(256 * 1024)
                if not block:
                    break
                yield block
        finally:
            resp.close()
    out["X-Name"] = f"{cam}-{day}-{file}"         # validated names: the route's download filename
    return resp.status, out, chunks()


# -- voice ------------------------------------------------------------------------------------------------------------
_J = r"(?:(?:hey\s+|ok(?:ay)?\s+)?jarvis[,.!]?\s+)?"
_ASK = r"(?:(?:can|could|would|will)\s+you\s+|please\s+|let'?s\s+)?"
_VERB = (r"(?:show(?:\s+me)?|open(?:\s+up)?|pull\s+up|bring\s+up|put\s+up|let\s+me\s+see|can\s+i\s+see|check(?:\s+on)?|"
         r"view|watch|look\s+at|give\s+me|switch\s+to|go\s+to)")
_TAIL = r"(?:\s+(?:please|now|for me|real quick|sir|jarvis))*[.!?]*"
_OWN = r"(?:(?:my|the|our)\s+)?"
_CAMWORD = r"(?:cam(?:era)?s?|feeds?|live\s+(?:view|feed)|security\s+cam(?:era)?s?)"
# "show me my house", "check on the house", "show house camera", "house cameras", "open the home cams"
_HOUSE_RE = re.compile(
    rf"{_J}{_ASK}{_VERB}\s+{_OWN}(?:house|home)(?:\s+{_CAMWORD})?{_TAIL}|"
    rf"{_J}{_ASK}(?:{_VERB}\s+)?{_OWN}(?:house|home|security)\s+{_CAMWORD}{_TAIL}|"
    rf"{_J}{_ASK}{_VERB}\s+(?:what'?s\s+(?:going\s+on|happening)\s+)?(?:at|in)\s+{_OWN}(?:house|home){_TAIL}", re.I)
# "show the living room camera", "living room cam", "let me see the living room"
_ROOM_CAM_RE = re.compile(rf"{_J}{_ASK}(?:{_VERB}\s+)?(?:(?P<own>my|our|the)\s+)?(?P<room>[a-z][a-z ]{{1,30}}?)\s+{_CAMWORD}{_TAIL}", re.I)
_ROOM_RE = re.compile(rf"{_J}{_ASK}{_VERB}\s+{_OWN}(?P<room>[a-z][a-z ]{{1,30}}?){_TAIL}", re.I)
_NOT_ROOMS = {"house", "home", "security", "my", "the", "web", "live", "landmark", "world", "front", "back", "phone", "pc",
              "computer", "laptop", "screen", "selfie"}


def match(text: str, known: list[str] | None = None) -> dict | None:
    """Is this line asking to see a house camera? {'cam': id or ''} ('' = the first/main one), {'unknown': room} for a
    room with no camera, or None. `known` = camera ids; fetched from the Pi only when the words already fit."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or len(t) > 120:
        return None
    if _HOUSE_RE.fullmatch(t):
        return {"cam": ""}
    m = _ROOM_CAM_RE.fullmatch(t)
    strict = m is None
    m = m or _ROOM_RE.fullmatch(t)
    if not m:
        return None
    room = re.sub(r"\s+", " ", m.group("room").strip().lower())
    words = room.split()
    if room in _NOT_ROOMS or len(words) > 3 or any(w in _STOP for w in words):
        return None
    if known is None:
        known = known_ids(wait=not strict)          # a camera word: worth asking the Pi; "show me the X": the cache only
    cam = room.replace(" ", "_")
    if cam in known:
        return {"cam": cam}
    # A camera that doesn't exist is only answered for HIS camera ("show my garage camera"); "show me the Eiffel Tower
    # cam" or "traffic cam" belongs to World Intelligence's live cams and other skills (review, 2026-11-17).
    if strict or (m.groupdict().get("own") or "").lower() not in ("my", "our"):
        return None
    return {"unknown": room}


_STOP = {"a", "an", "the", "my", "our", "your", "his", "her", "their", "this", "that", "on", "off", "in", "at", "of", "to",
         "with", "for", "from", "and", "or", "turn", "take", "use", "start", "stop", "is", "it", "me", "up"}
_known: dict = {"at": -1e9, "ids": [], "ok": False}


def _refresh_known() -> None:
    try:
        ids, ok = [c["id"] for c in cameras()], True
    except CameraError:
        ids, ok = _known["ids"], False
    _known.update(at=time.monotonic(), ids=ids, ok=ok, busy=False)


def known_ids(wait: bool = False) -> list[str]:
    """Camera ids for matching words, cached (5 min; after a failure 60 s). Without wait a stale cache is refreshed in
    the background and the cached ids answer now, so "open Chrome" never waits on the Pi."""
    if time.monotonic() - _known["at"] < (300 if _known["ok"] else 60):
        return _known["ids"]
    if wait:
        _refresh_known()
    elif not _known.get("busy"):
        _known["busy"] = True
        threading.Thread(target=_refresh_known, daemon=True, name="homecam-ids").start()
    return _known["ids"]


def _owner() -> bool:
    try:
        from core import users
        return users.get(users.current()).get("role") == "owner"
    except Exception:  # noqa: BLE001
        return False


def _sir() -> str:
    try:
        from core import users
        return users.title(users.current(), "en") or "sir"
    except Exception:  # noqa: BLE001
        return "sir"


def _ago(ts) -> str:
    if not ts:
        return ""
    mins = int((time.time() - float(ts)) // 60)
    if mins < 2:
        return "a moment ago"
    if mins < 90:
        from core.words import plural
        return f"{plural(mins, 'minute')} ago"
    return time.strftime("%I:%M %p on %b %d", time.localtime(float(ts))).lstrip("0")


def voice(text: str) -> str | None:
    """'Show me my house' / 'show the living room camera': open the camera view where he asked (the Hub on the PC; on the
    phone server the reply carries a link, see chat_extras). None when the line isn't a camera request."""
    hit = match(text)
    if hit is None:
        return None
    sir = _sir()
    if not _owner():
        return "The house cameras are only for the owner."
    if "unknown" in hit:
        try:
            names = ", ".join(c["label"].lower() for c in cameras()) or "none yet"
        except CameraError:
            names = "I couldn't reach the recorder to check"
        return f"There's no {hit['unknown']} camera, {sir}. Cameras: {names}."
    try:
        cams = cameras()
    except CameraError as e:
        cams, problem = [], str(e)
    else:
        problem = ""
    cam = hit["cam"] or (cams[0]["id"] if cams else "living_room")
    info = next((c for c in cams if c["id"] == cam), None)
    where = label(cam).lower()
    if problem:
        state = f" {problem}"
    elif info is None:
        state = f" The recorder doesn't know a {where} camera."
    elif info["recording"]:
        state = ""
    else:
        last = _ago(info.get("last_video_at"))
        state = f" It isn't sending video right now" + (f"; the last recording was {last}." if last else ", and nothing has been recorded yet.")
    if os.getenv("JARVIS_ROLE") == "server":
        return f"Here's the {where}, {sir}. Tap the link for the live view." + state
    from core import hud
    if not hud.house(cam):
        return f"The Command Center isn't running on this PC, {sir}." + state
    return f"Here's the {where}, {sir}." + state


def chat_extras(text: str) -> dict:
    """For the phone app: a link to the live page and a current frame, when the line asked for a house camera and the
    asker is the owner. Empty otherwise (and on the PC, where the Hub opens instead)."""
    try:
        hit = match(text)
        if not hit or "unknown" in hit or not _owner():
            return {}
        cam = hit["cam"]
        if not cam:
            cams = cameras()
            cam = cams[0]["id"] if cams else ""
        if not cam:
            return {}
        out = {"link": {"label": f"{label(cam)}: live view", "page": f"house.html?cam={cam}"}}
        info = next((c for c in cameras() if c["id"] == cam), None)
        if info and info["recording"]:
            out["images"] = [{"url": f"/api/house/frame/{cam}", "caption": f"{label(cam)}, just now"}]
        return out
    except CameraError:
        return {}


# -- detection events (Frigate on the Pi: motion + people, pets, cars) ----------------------------------------------
SEGMENT_SECONDS = int(os.getenv("HOMECAM_SEGMENT_SECONDS", "300"))    # the recorder's segment length
EVENT_ID = re.compile(r"^[0-9]{9,11}\.[0-9]{1,7}-[a-z0-9]{4,12}$")
LABELS = {"person": "person", "dog": "dog", "cat": "cat", "car": "car", "bark": "barking", "scream": "screaming",
          "yell": "yelling", "fire_alarm": "fire alarm"}
_day_cache: dict = {}


def _segments_of(cam: str, day: str) -> list[str]:
    """Segment file names of one camera and day, sorted, cached 30 s (an event list maps many events to them)."""
    key, now = (cam, day), time.monotonic()
    hit = _day_cache.get(key)
    if hit and now - hit[0] < 30:
        return hit[1]
    try:
        names = sorted(f["name"] for f in _listing(f"/recordings/{cam}/{day}/")
                       if f.get("type") == "file" and SEGMENT.fullmatch(str(f.get("name"))))
    except CameraError:
        names = []
    _day_cache[key] = (now, names)
    return names


def seg_len(name: str) -> int:
    """How many seconds a recorded file holds: a card clip says so in its name, a live segment is SEGMENT_SECONDS."""
    m = re.search(r"-card-(\d{1,4})\.mp4$", name)
    return int(m.group(1)) if m else SEGMENT_SECONDS


def recording_at(cam: str, ts: float) -> dict | None:
    """The recorded segment that holds a moment, and how far into it: {day, file, offset, url}, or None."""
    lt = time.localtime(float(ts))
    day, clock = time.strftime("%Y-%m-%d", lt), time.strftime("%H-%M-%S", lt) + ".mp4"
    names = [n for n in _segments_of(cam, day) if n <= clock]
    if not names:                                         # just after midnight: the segment began yesterday
        prev = time.localtime(float(ts) - 3 * 3600)
        if time.strftime("%Y-%m-%d", prev) == day:
            return None
        day, lt = time.strftime("%Y-%m-%d", prev), prev
        names = _segments_of(cam, day)
        if not names:
            return None
    name = names[-1]
    h, m, s = (int(x) for x in name[:8].split("-"))
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, s, 0, 0, -1))
    offset = max(0, int(float(ts) - start) - 3)          # a few seconds before, so the moment isn't missed
    if float(ts) - start > seg_len(name) + 30:            # no segment covers it (a gap in the recording)
        return None
    return {"day": day, "file": name, "offset": offset, "url": f"/api/house/recording/{cam}/{day}/{name}"}


def events(cam: str, limit: int = 50, since: float | None = None) -> list[dict]:
    """What Frigate saw on one camera, newest first: {id, label, start, end, score, snapshot, recording}."""
    cam = _check(cam)
    q = f"camera={cam}&limit={max(1, min(int(limit), 200))}&include_thumbnails=0"
    if since:
        q += f"&after={int(since)}"
    try:
        with _open(f"/events?{q}", timeout=8) as r:
            raw = json.loads(r.read().decode("utf-8") or "[]")
    except CameraError as e:
        raise CameraError("The motion and person detector on the Pi isn't answering right now.", e.status) from None
    out = []
    for ev in raw if isinstance(raw, list) else []:
        eid, lab = str(ev.get("id", "")), str(ev.get("label", ""))
        if not EVENT_ID.fullmatch(eid) or lab not in LABELS or not ev.get("start_time"):
            continue
        score = ev.get("top_score") or (ev.get("data") or {}).get("top_score") or ev.get("score") or 0
        out.append({"id": eid, "label": lab, "start": float(ev["start_time"]),
                    "end": float(ev["end_time"]) if ev.get("end_time") else None, "score": round(float(score or 0), 2),
                    "snapshot": f"/api/house/event/{eid}.jpg" if ev.get("has_snapshot") else None,
                    "recording": recording_at(cam, float(ev["start_time"]))})
    return out


def event_snapshot(eid: str) -> bytes:
    if not EVENT_ID.fullmatch(eid or ""):
        raise CameraError("No such event.", 404)
    with _open(f"/events/{eid}/snapshot.jpg", timeout=10) as r:
        return r.read(5_000_000)


def _today_start() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def activity_summary(cam: str | None = None, since: float | None = None) -> str:
    """'Detected today: 3 people in the living room. The last one was at 1:52 AM.' in words he can hear."""
    from core.words import plural
    since = since or _today_start()
    cams = [cam] if cam else [c["id"] for c in cameras()]
    parts, last_ts = [], 0.0
    for c in cams:
        counts: dict = {}
        for ev in events(c, 200, since):
            counts[ev["label"]] = counts.get(ev["label"], 0) + 1
            last_ts = max(last_ts, ev["start"])
        if counts:
            what = ", ".join(plural(n, k, "people" if k == "person" else None)
                             for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
            parts.append(f"{what} in the {label(c).lower()}")
    when = "today" if since >= _today_start() else "since " + time.strftime("%I:%M %p", time.localtime(since)).lstrip("0")
    if not parts:
        return f"Nothing has been detected {when}: no people, pets or cars on the cameras."
    last = time.strftime("%I:%M %p", time.localtime(last_ts)).lstrip("0")
    return f"Detected {when}: {'; '.join(parts)}. The last one was at {last}."


# -- pan / tilt (the card's /debug/www/cgi-bin/ptz behind camgate) ---------------------------------------------------
_pos: dict = {}                              # last known pan/tilt per camera, from its own answers
PTZ_CMDS = ("left", "right", "up", "down", "left_up", "right_up", "left_down", "right_down", "t2p", "home", "day",
            "night", "pos")


def ptz(cam: str, cmd: str, pan=None, tilt=None) -> dict:
    """Turn a camera, or switch its day/night filter. {ok, pan, tilt, day}."""
    cam = _check(cam)
    cmd = str(cmd or "").strip().lower()
    if cmd not in PTZ_CMDS:
        raise CameraError("I don't know that camera move.", 400)
    q = f"c={cmd}"
    if cmd == "t2p":
        try:
            p, t = int(pan), int(tilt)
        except (TypeError, ValueError):
            raise CameraError("Aiming needs a pan and a tilt angle.", 400) from None
        q += f"&p={max(0, min(p, 355))}&t={max(0, min(t, 180))}"
    where = label(cam).lower()
    try:
        with _open(f"/ptz/{cam}?{q}", timeout=12) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
    except CameraError as e:
        if e.status == 404:
            raise CameraError(f"The {where} camera can't turn yet: its card needs the pan and tilt update.", 404) from None
        raise CameraError(f"The {where} camera isn't answering turn commands. Until its card has the pan and tilt "
                          "update, it can't turn.", 503) from None
    except ValueError:
        raise CameraError(f"The {where} camera gave a garbled answer.", 502) from None
    if not data.get("ok"):
        raise CameraError(f"The {where} camera refused the move ({data.get('error', 'no reason')}).", 502)
    out = {"ok": True, "pan": data.get("pan"), "tilt": data.get("tilt"), "day": data.get("day")}
    if isinstance(out["pan"], int) and isinstance(out["tilt"], int):
        _pos[cam] = (out["pan"], out["tilt"], time.monotonic())   # tap-to-aim uses it: no extra round trip
    return out


PRESET_NAME = re.compile(r"^[a-z][a-z0-9 ]{0,23}$")


def presets(cam: str) -> dict:
    from core import storage
    return dict((storage.load_global("homecam_presets.json", {}) or {}).get(_check(cam), {}))


def save_preset(cam: str, name: str) -> dict:
    """Remember where the camera points now under a name ('door', 'couch')."""
    from core import storage
    cam, name = _check(cam), re.sub(r"\s+", " ", str(name or "").strip().lower())
    if not PRESET_NAME.fullmatch(name):
        raise CameraError("Give the position a short name, like door or couch.", 400)
    pos = ptz(cam, "pos")
    allp = storage.load_global("homecam_presets.json", {}) or {}
    mine = allp.setdefault(cam, {})
    if name not in mine and len(mine) >= 12:
        raise CameraError("That camera already has 12 saved positions; remove one first.", 400)
    mine[name] = [int(pos["pan"]), int(pos["tilt"])]
    storage.save_global("homecam_presets.json", allp)
    return mine


def delete_preset(cam: str, name: str) -> dict:
    from core import storage
    cam, name = _check(cam), str(name or "").strip().lower()
    allp = storage.load_global("homecam_presets.json", {}) or {}
    allp.get(cam, {}).pop(name, None)
    storage.save_global("homecam_presets.json", allp)
    return allp.get(cam, {})


def go_preset(cam: str, name: str) -> dict:
    cam, name = _check(cam), str(name or "").strip().lower()
    spot = presets(cam).get(name)
    if spot is None:
        if name in ("home", "center", "centre", "middle", "default"):
            spot = [180, 90]
        else:
            raise CameraError(f"There's no saved camera position called {name}.", 404)
    return ptz(cam, "t2p", spot[0], spot[1])


# -- voice: turning the camera, and "did anything happen at home?" ---------------------------------------------------
_CAM = r"(?:(?:my|the|our)\s+)?(?:(?P<room>[a-z][a-z ]{1,20}?)\s+)?(?:camera|cam)"
_DIR = r"(?P<dir>left|right|up|down)"
_PTZ_RE = re.compile(
    rf"{_J}{_ASK}(?:turn|move|pan|tilt|point|rotate|swing|aim)\s+{_CAM}\s+(?:(?:to\s+the|a\s+(?:bit|little)|more|over)\s+)?{_DIR}"
    rf"(?:\s+(?:a\s+(?:bit|little)|more))?{_TAIL}", re.I)
_PTZ2_RE = re.compile(rf"{_J}{_CAM}\s+{_DIR}{_TAIL}", re.I)           # "camera left"
_AIM_RE = re.compile(rf"{_J}{_ASK}(?:point|aim|turn|move|swing)\s+{_CAM}\s+(?:at|to|toward|towards)\s+(?:the\s+)?(?P<spot>[a-z][a-z0-9 ]{{0,23}}?){_TAIL}", re.I)
_CENTER_RE = re.compile(rf"{_J}{_ASK}(?:center|centre|reset|recenter|straighten)\s+{_CAM}{_TAIL}", re.I)
_SAVE_RE = re.compile(rf"{_J}{_ASK}(?:save|remember|store)\s+(?:this|the|that)?\s*(?:camera\s+)?(?:position|view|spot|angle)\s+as\s+(?:the\s+)?(?P<spot>[a-z][a-z0-9 ]{{0,23}}?){_TAIL}", re.I)
_NIGHT_RE = re.compile(rf"{_J}{_ASK}(?:(?:turn|switch|put)\s+)?{_CAM}\s+(?:to\s+|on\s+|in\s+)?(?:night|day)\s*(?:mode|vision)?(?:\s+on)?{_TAIL}", re.I)
_NIGHT2_RE = re.compile(rf"{_J}{_ASK}(?:night|day)\s+(?:mode|vision)\s+(?:on\s+)?(?:for\s+)?{_CAM}{_TAIL}", re.I)
_ACTIVITY_RE = re.compile(
    rf"{_J}{_ASK}(?:(?:did|has)\s+anything\s+(?:happen(?:ed)?|go(?:ne)?\s+on|been\s+going\s+on)|any\s+(?:motion|movement|activity|people|one)|"
    r"(?:what(?:'s|\s+has)?|what)\s+happened|who\s+(?:was|came|has\s+been)|was\s+any(?:one|body)|"
    r"(?:did|has)\s+any(?:one|body)\s+(?:come|been))\s*(?:in(?:to)?|at|around)?\s*(?:the\s+|my\s+|our\s+)?"
    r"(?P<where>house|home|living\s+room|(?!(?:today|tonight|last|while|on)\b)[a-z]+(?:\s+room)?)?"
    r"(?:\s+(?:today|tonight|last\s+night|while\s+i\s+was\s+(?:gone|out|away|asleep|sleeping)))?(?:\s+(?:on\s+the\s+)?cameras?)?[.!?]*", re.I)


def _cam_for(room: str | None) -> str:
    """The camera a spoken room names, or the first camera for none / 'house'."""
    ids = known_ids(wait=True)
    room = re.sub(r"\s+", "_", (room or "").strip().lower())
    if room in ids:
        return room
    if room and room not in ("house", "home", "security"):
        raise CameraError(f"There's no {room.replace('_', ' ')} camera.", 404)
    return ids[0] if ids else "living_room"


def control_voice(text: str) -> str | None:
    """Camera moves and activity questions said to Jarvis. None when the line is neither."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or len(t) > 120:
        return None
    m_ptz, m_aim, m_ctr = _PTZ_RE.fullmatch(t) or _PTZ2_RE.fullmatch(t), _AIM_RE.fullmatch(t), _CENTER_RE.fullmatch(t)
    m_save, m_night = _SAVE_RE.fullmatch(t), _NIGHT_RE.fullmatch(t) or _NIGHT2_RE.fullmatch(t)
    m_act = _ACTIVITY_RE.fullmatch(t)
    if not any((m_ptz, m_aim, m_ctr, m_save, m_night, m_act)):
        return None
    if m_act and not m_act.group("where") is None and m_act.group("where").lower() not in ("house", "home"):
        if m_act.group("where").lower().replace(" ", "_") not in known_ids(wait=True):
            return None                              # "who was in the meeting": not a camera question
    if m_act and not m_act.group("where") and re.match(r"\W*(?:(?:hey\s+)?jarvis\W+)?(?:what|who)\b", t, re.I) \
            and not any((m_ptz, m_aim, m_ctr, m_save, m_night)):
        return None                                 # "what happened today?" could be the news: needs a place
    m_room = next((m for m in (m_ptz, m_aim, m_ctr, m_save, m_night) if m), None)
    room = ((m_room.groupdict().get("room") or "") if m_room else "").strip().lower()
    if room and room not in ("house", "home", "security") and room.replace(" ", "_") not in known_ids(wait=True):
        return None                                  # "move my phone camera up", "switch camera left": not a house camera
    if not _owner():
        return "The house cameras are only for the owner."
    sir = _sir()
    try:
        if m_act:
            where = (m_act.group("where") or "").lower()
            cam = None if where in ("", "house", "home") else where.replace(" ", "_")
            since = None
            if re.search(r"last night|while i was asleep|while i was sleeping", t, re.I):
                since = _today_start() - 6 * 3600
            return activity_summary(cam, since)
        m = m_ptz or m_aim or m_ctr or m_save or m_night
        cam = _cam_for(m.groupdict().get("room"))
        where = label(cam).lower()
        if m_save:
            save_preset(cam, m_save.group("spot"))
            return f"Saved. Say \"point the camera at {m_save.group('spot').lower()}\" to come back to it, {sir}."
        if m_night:
            mode = "night" if re.search(r"\bnight\b", t, re.I) else "day"
            ptz(cam, mode)
            return f"The {where} camera is in {mode} mode, {sir}."
        if m_ctr:
            go_preset(cam, "home")
            return f"Centering the {where} camera, {sir}."
        if m_aim and not m_ptz:
            spot = m_aim.group("spot").lower()
            go_preset(cam, spot)
            return f"Pointing the {where} camera at the {spot}, {sir}."
        d = m_ptz.group("dir").lower()
        move(cam, d)
        return f"Turning the {where} camera {d}, {sir}."
    except CameraError as e:
        return f"{e}"


# -- talk-back: his voice (or Jarvis's) out of the camera's speaker --------------------------------------------------
TALK_MAX = 4_000_000


def _post(path: str, data: bytes, ctype: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(gate() + path, data=data, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(5_000_000)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise CameraError("The camera gateway on the Pi refused this computer.", 502) from None
        if e.code == 404:
            raise CameraError("That camera can't talk yet: its card needs the talk-back update.", 404) from None
        if e.code == 422:
            raise CameraError("That recording couldn't be turned into sound for the camera.", 422) from None
        raise CameraError(f"The camera gateway answered {e.code}.", 502) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CameraError("I can't reach the camera recorder on the Pi.", 503) from None


def talk(cam: str, audio: bytes) -> dict:
    """Play a recording (his push-to-talk clip, or Jarvis's speech) out of the camera's speaker."""
    cam = _check(cam)
    if not audio or len(audio) < 200:
        raise CameraError("That was too short to send.", 400)
    if len(audio) > TALK_MAX:
        raise CameraError("That message is too long; keep it under a minute.", 413)
    mp3 = _post("/convert", audio, "application/octet-stream")
    try:
        data = json.loads(_post(f"/speak/{cam}", mp3, "audio/mpeg", timeout=20).decode("utf-8") or "{}")
    except CameraError as e:
        if e.status == 404 or e.status >= 500:
            raise CameraError(f"The {label(cam).lower()} camera can't talk yet: its card needs the talk-back update.", 404) from None
        raise
    except ValueError:
        raise CameraError("The camera gave a garbled answer.", 502) from None
    if not data.get("ok"):
        raise CameraError(f"The {label(cam).lower()} camera refused the message ({data.get('error', 'no reason')}).", 502)
    return {"ok": True, "bytes": len(mp3)}


def announce(cam: str, text: str) -> dict:
    """Jarvis says something out of the camera, in his own voice."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:300]
    if not text:
        raise CameraError("What should I say?", 400)
    from audio.tts import mp3_for
    return talk(cam, mp3_for(text))


_ANNOUNCE_RE = re.compile(
    rf"{_J}(?:announce|say|tell\s+(?:them|everyone|the\s+(?:room|house)))\s+(?:(?:on|through|over|from)\s+{_CAM}\s*[,:]?\s+)?(?P<msg>.{{2,200}}?)"
    rf"(?:\s+(?:on|through|over|from)\s+{_CAM.replace('?P<room>', '?P<room2>')})?[.!]?", re.I)


def announce_voice(text: str) -> str | None:
    """'Announce on the living room camera: dinner is ready' / 'say dinner is ready through the camera'."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    m = _ANNOUNCE_RE.fullmatch(t) if t and len(t) < 260 else None
    if not m or not re.search(r"\b(?:camera|cam)\b", t, re.I):
        return None                                  # "say hello" alone is a conversation, not a camera announcement
    if not _owner():
        return "The house cameras are only for the owner."
    msg = m.group("msg").strip(" ,:")
    try:
        cam = _cam_for(m.group("room") or m.group("room2"))
        announce(cam, msg)
        return f"Said it on the {label(cam).lower()} camera, {_sir()}."
    except CameraError as e:
        return f"{e}"


# -- settings: alerts, follow mode, flipped arrows -------------------------------------------------------------------
SETTINGS_FILE = "homecam_settings.json"
SOUND_LABELS = {"bark": "barking", "scream": "screaming", "yell": "yelling", "fire_alarm": "a fire alarm"}
ALERTABLE = ("person", "dog", "cat", "car") + tuple(SOUND_LABELS)
FOV_H, FOV_V = 88.0, 50.0                              # the CB401's field of view, for aiming at a point of the picture


def settings() -> dict:
    from core import storage
    s = storage.load_global(SETTINGS_FILE, {}) or {}
    alerts = s.get("alerts") or {}
    return {"alerts": {"on": bool(alerts.get("on", False)),
                       "labels": [x for x in alerts.get("labels", ["person", "fire_alarm", "scream"]) if x in ALERTABLE],
                       "night": bool(alerts.get("night", False))},
            "follow": {k: bool(v) for k, v in (s.get("follow") or {}).items() if NAME.fullmatch(str(k))},
            "flip": {k: {"pan": bool((v or {}).get("pan")), "tilt": bool((v or {}).get("tilt"))}
                     for k, v in (s.get("flip") or {}).items() if NAME.fullmatch(str(k))},
            "privacy_return": dict(s.get("privacy_return") or {})}


def update_settings(patch: dict) -> dict:
    """Change only what's given: {alerts: {on, labels, night}}, {follow: {cam: bool}}, {flip: {cam: {pan, tilt}}}."""
    from core import storage
    cur = settings()
    if isinstance(patch.get("alerts"), dict):
        a = patch["alerts"]
        if "on" in a:
            cur["alerts"]["on"] = bool(a["on"])
        if "night" in a:
            cur["alerts"]["night"] = bool(a["night"])
        if isinstance(a.get("labels"), list):
            cur["alerts"]["labels"] = [x for x in a["labels"] if x in ALERTABLE][:12]
    for key in ("follow", "flip"):
        for cam, val in (patch.get(key) or {}).items() if isinstance(patch.get(key), dict) else []:
            if NAME.fullmatch(str(cam)):
                cur[key][cam] = bool(val) if key == "follow" else {"pan": bool((val or {}).get("pan")),
                                                                      "tilt": bool((val or {}).get("tilt"))}
    storage.save_global(SETTINGS_FILE, cur)
    return cur


def _flip(cam: str) -> dict:
    return settings()["flip"].get(cam, {"pan": False, "tilt": False})


_SWAP_H = {"left": "right", "right": "left", "left_up": "right_up", "right_up": "left_up", "left_down": "right_down",
           "right_down": "left_down"}
_SWAP_V = {"up": "down", "down": "up", "left_up": "left_down", "left_down": "left_up", "right_up": "right_down",
           "right_down": "right_up"}


def move(cam: str, direction: str) -> dict:
    """A direction as he sees it on screen, corrected for a camera whose motors turn the other way."""
    f, d = _flip(_check(cam)), str(direction or "")
    if f["pan"]:
        d = _SWAP_H.get(d, d)
    if f["tilt"]:
        d = _SWAP_V.get(d, d)
    return ptz(cam, d)


def aim(cam: str, dx: float, dy: float) -> dict:
    """Turn so the point (dx, dy) of the picture (-0.5..0.5 from the centre) becomes the centre."""
    cam = _check(cam)
    try:
        dx, dy = max(-0.5, min(float(dx), 0.5)), max(-0.5, min(float(dy), 0.5))
    except (TypeError, ValueError):
        raise CameraError("Aiming needs a point of the picture.", 400) from None
    f, known = _flip(cam), _pos.get(cam)
    pos = {"pan": known[0], "tilt": known[1]} if known and time.monotonic() - known[2] < 60 else ptz(cam, "pos")
    pan = int(pos["pan"]) + round(dx * FOV_H) * (-1 if f["pan"] else 1)
    tilt = int(pos["tilt"]) + round(dy * FOV_V) * (-1 if f["tilt"] else 1)
    return ptz(cam, "t2p", pan, tilt)


# -- privacy: recording paused on the Pi, camera looking at its own base, live view refused ------------------------
PRIVACY_POSE = (180, 180)


def privacy_on(cam: str) -> bool:
    try:
        return bool((status().get("cameras") or {}).get(_check(cam), {}).get("privacy"))
    except CameraError:
        return False


def set_privacy(cam: str, on: bool) -> dict:
    from core import storage
    cam = _check(cam)
    moved = ""
    if on:
        try:                                            # remember where it looked, then look at the base
            pos = ptz(cam, "pos")
            s = settings()
            s["privacy_return"][cam] = [int(pos["pan"]), int(pos["tilt"])]
            storage.save_global(SETTINGS_FILE, s)
            ptz(cam, "t2p", *PRIVACY_POSE)
            moved = "turned away"
        except CameraError:
            moved = "can't turn (recording and live view are still stopped)"
    _post(f"/privacy/{cam}?on={1 if on else 0}", b"{}", "application/json", timeout=10)
    _cache["status"] = None
    if not on:
        back = settings()["privacy_return"].get(cam)
        try:
            if back:
                ptz(cam, "t2p", back[0], back[1])
                moved = "back where it was"
        except CameraError:
            pass
    return {"ok": True, "privacy": bool(on), "camera": moved}


def _not_private(cam: str) -> None:
    if privacy_on(cam):
        raise CameraError("Privacy mode is on for that camera.", 423)


# -- background watcher on the always-on phone server: alerts and follow mode ----------------------------------------
_monitor_started = threading.Event()
_last_move: dict = {}


def _alert_text(cam: str, ev: dict) -> tuple[str, str]:
    where, lab = label(cam).lower(), ev["label"]
    if lab in SOUND_LABELS:
        return "House: sound", f"I heard {SOUND_LABELS[lab]} in the {where}."
    noun = {"person": "Someone", "dog": "A dog", "cat": "A cat", "car": "A car"}.get(lab, "Something")
    return "House: " + lab, f"{noun} in the {where}, {time.strftime('%I:%M %p').lstrip('0')}."


def check_alerts(since: float, seen: set, sent: dict, now: float | None = None) -> int:
    """Send a phone alert for each new event he asked to hear about; one per camera and kind every 10 minutes."""
    from core import notify
    s, now = settings(), now or time.time()
    if not s["alerts"]["on"]:
        return 0
    count = 0
    for c in cameras():
        if c.get("privacy"):
            continue
        for ev in events(c["id"], 30, since):
            if ev["id"] in seen or ev["label"] not in s["alerts"]["labels"]:
                continue
            seen.add(ev["id"])
            key = (c["id"], ev["label"])
            if now - sent.get(key, 0) < 600:
                continue
            sent[key] = now
            title, body = _alert_text(c["id"], ev)
            for uid in camera_users():                    # the owner AND his wife (each one's own notification settings apply)
                notify.notify(title, body, uid=uid, category="house",
                              urgent=True if s["alerts"]["night"] or ev["label"] == "fire_alarm" else None,
                              extra={"url": f"/house.html?cam={c['id']}", "tag": f"house-{c['id']}-{ev['label']}"})
            count += 1
    if len(seen) > 2000:
        seen.clear()
    return count


def follow_step(cam: str, now: float | None = None) -> str:
    """Follow mode: keep the person Frigate is tracking right now near the middle of the picture."""
    now = now or time.time()
    if now - _last_move.get(cam, 0) < 3:
        return "cooling"
    try:
        with _open(f"/events?camera={cam}&label=person&in_progress=1&limit=1", timeout=5) as r:
            raw = json.loads(r.read().decode("utf-8") or "[]")
    except (CameraError, ValueError):
        return "no events"
    box = ((raw[0].get("data") or {}).get("box") if raw and isinstance(raw, list) else None) or []
    if len(box) != 4:
        return "nobody"
    cx, cy = float(box[0]) + float(box[2]) / 2, float(box[1]) + float(box[3]) / 2
    dx, dy = cx - 0.5, cy - 0.5
    if abs(dx) < 0.13 and abs(dy) < 0.2:
        return "centred"
    aim(cam, dx if abs(dx) >= 0.13 else 0.0, dy if abs(dy) >= 0.2 else 0.0)
    _last_move[cam] = now
    return "moved"


def monitor_loop(stop: threading.Event | None = None) -> None:
    seen, sent, since = set(), {}, time.time() - 60
    stop = stop or threading.Event()
    while not stop.is_set():
        following = False
        try:
            started = time.time()
            check_alerts(since, seen, sent)
            since = started - 30                     # overlap: an event that started just before is still caught
            s = settings()
            for cam, on in s["follow"].items():
                if on and not privacy_on(cam):
                    following = True
                    follow_step(cam)
        except Exception as e:  # noqa: BLE001 - the watcher must outlive a bad answer from the Pi
            print(f"[house] monitor: {type(e).__name__}: {e}")
        stop.wait(1.5 if following else 15)


def start_monitor() -> bool:
    """Once per process, on the always-on phone server only (the PC never alerts twice)."""
    if _monitor_started.is_set() or os.getenv("JARVIS_ROLE") != "server" or os.getenv("HOMECAM_MONITOR", "1") == "0":
        return False
    _monitor_started.set()
    threading.Thread(target=monitor_loop, daemon=True, name="house-monitor").start()
    return True


# -- voice: privacy, alerts, follow ----------------------------------------------------------------------------------
_ONOFF = r"(?P<v>on|off|enable|disable|start|stop|pause|resume)"
_PRIV_RE = re.compile(rf"{_J}{_ASK}(?:(?:turn|switch|put)\s+)?(?:(?:the\s+)?(?:(?P<room>[a-z][a-z ]{{1,20}}?)\s+)?camera\s+)?privacy(?:\s+mode)?\s+{_ONOFF}{_TAIL}|"
                      rf"{_J}{_ASK}(?P<v2>turn on|turn off|enable|disable|start|stop)\s+(?:the\s+)?(?:camera\s+)?privacy(?:\s+mode)?{_TAIL}", re.I)
_ALERT_RE = re.compile(rf"{_J}{_ASK}(?:(?P<v2>turn on|turn off|enable|disable|mute|unmute|stop|start)\s+(?:the\s+)?(?:house\s+|home\s+)?camera\s+(?:alerts?|notifications?)|"
                       rf"(?:house\s+|home\s+)?camera\s+(?:alerts?|notifications?)\s+{_ONOFF}){_TAIL}", re.I)
_FOLLOW_RE = re.compile(rf"{_J}{_ASK}(?:(?P<start>follow|track)\s+(?:people|anyone|someone|me|whoever)(?:\s+with\s+the\s+camera)?|"
                        r"(?:camera\s+)?follow(?:\s+mode)?\s+(?P<v>on|off)|(?P<stop>stop)\s+(?:following|tracking)(?:\s+people)?)"
                        rf"{_TAIL}", re.I)


def _is_on(word: str) -> bool:
    return (word or "").lower() in ("on", "enable", "start", "turn on", "unmute", "resume")


def settings_voice(text: str) -> str | None:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or len(t) > 100:
        return None
    mp, ma, mf = _PRIV_RE.fullmatch(t), _ALERT_RE.fullmatch(t), _FOLLOW_RE.fullmatch(t)
    if not (mp or ma or mf):
        return None
    if not _owner():
        return "The house cameras are only for the owner."
    sir = _sir()
    try:
        if mp:
            word = mp.group("v") or mp.group("v2")
            on = _is_on(word) or word.lower() in ("pause",)
            if word.lower() == "resume":
                on = False
            cam = _cam_for(mp.group("room"))
            r = set_privacy(cam, on)
            return (f"Privacy mode is on for the {label(cam).lower()}, {sir}: recording paused, live view off, camera "
                    f"{r['camera'] or 'left as it is'}." if on else
                    f"Privacy mode is off. The {label(cam).lower()} camera is recording again, {sir}.")
        if ma:
            on = _is_on(ma.group("v2") or ma.group("v"))
            update_settings({"alerts": {"on": on}})
            return f"Camera alerts are {'on' if on else 'off'}, {sir}."
        on = bool(mf.group("start")) or (mf.group("v") or "").lower() == "on"
        cam = _cam_for(None)
        update_settings({"follow": {cam: on}})
        return (f"Following people with the {label(cam).lower()} camera, {sir}." if on else
                f"Stopped following, {sir}.")
    except CameraError as e:
        return f"{e}"
