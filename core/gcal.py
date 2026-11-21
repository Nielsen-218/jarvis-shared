"""Google Calendar for Jarvis: read the owner's schedule and add, move or cancel events.

Plain REST over httpx (no Google client library). The one-time sign-in is `scripts/google_calendar_setup.py`: a
browser consent with PKCE on a loopback port, the way Google wants desktop apps to do it. The resulting refresh token,
with the OAuth client id/secret, is kept per person in data/users/<id>/google_calendar.json, so it syncs to the
laptop with the rest of data/ and the phone path can read the calendar too. Nothing here sends the token anywhere but
Google's own token and calendar endpoints.

Times: tools hand us naive local ISO strings ('2026-11-20T15:00'); we send them with the owner's IANA zone
(core.ownertime) and Google does the DST math. Read ranges are sent as RFC 3339 with the owner's current UTC offset.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from core import ownertime, storage

TOKEN_FILE = "google_calendar.json"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
CALENDAR = "primary"
TIMEOUT = 15


class CalendarError(Exception):
    """A calendar problem worded for Jarvis to say out loud."""


NOT_CONNECTED = ("Your Google Calendar isn't connected yet. Open the Jarvis app, tap Calendar, and follow the "
                 "steps there, then ask me again.")
CLIENT_FILE = "household/google_client.json"   # the family's one Google 'Desktop app' key, shared so each person only taps Allow


# -- sign-in -------------------------------------------------------------------------------
def load_client(path: str) -> dict:
    """The OAuth client from the JSON file Google Cloud Console downloads (a 'Desktop app' client)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    info = raw.get("installed") or raw.get("web") or raw
    if not info.get("client_id") or not info.get("client_secret"):
        raise CalendarError("That file has no client_id/client_secret. Download the 'Desktop app' OAuth client JSON.")
    return {"client_id": info["client_id"], "client_secret": info["client_secret"]}


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize(client: dict, open_browser=webbrowser.open, timeout: float = 300) -> dict:
    """Run the browser consent once and save the token for the current person. Returns the saved record."""
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            if q.get("state", [""])[0] != state:  # not our redirect (a stray or hostile local request): keep waiting
                self.send_response(400)
                self.end_headers()
                return
            got.update({k: v[0] for k, v in q.items()})
            ok = "code" in q
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>Jarvis is connected to your calendar. You can close this tab.</h2>" if ok else
                              "<h2>Calendar sign-in did not finish. Go back to the terminal.</h2>").encode())

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    worker = threading.Thread(target=lambda: _serve_until(server, got, timeout), daemon=True)
    worker.start()
    print("Opening Google sign-in in your browser. If it doesn't open, paste this link:\n" + url)
    open_browser(url)
    worker.join(timeout + 5)
    server.server_close()
    if got.get("error"):
        raise CalendarError(f"Google said: {got['error']}.")
    if "code" not in got:
        raise CalendarError("No answer from the browser in time. Run the setup again.")
    if got.get("state") != state:
        raise CalendarError("The sign-in answer didn't match this request (state mismatch). Run the setup again.")
    r = httpx.post(TOKEN_URL, data={
        "code": got["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}, timeout=TIMEOUT)
    if r.status_code != 200:
        raise CalendarError(f"Google refused the sign-in code ({r.status_code}).")
    tok = _json(r)
    if not tok.get("refresh_token"):
        raise CalendarError("Google gave no refresh token. Remove Jarvis at myaccount.google.com/permissions and retry.")
    record = {**client, "refresh_token": tok["refresh_token"], "access_token": tok.get("access_token", ""),
              "expires_at": time.time() + int(tok.get("expires_in", 0)) - 60, "scope": tok.get("scope", SCOPE)}
    storage.save(TOKEN_FILE, record)
    return record


# -- tap-through sign-in from the Jarvis app (app/calendar.html) -------------------------------------------------
# The page saves the family's Google key once (save_client), then start_link() gives a Google consent link. Google sends
# the browser back to a loopback address: on the PC that lands on a listener started here and finishes by itself; on
# the phone the page shows "can't be reached" and he pastes that address back (finish_link). The state is single-use
# and ties the answer to the person who started it.
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()
LINK_TTL = 900


def saved_client() -> dict:
    c = storage.load(CLIENT_FILE, {})
    return c if isinstance(c, dict) and c.get("client_id") and c.get("client_secret") else {}


def save_client(raw: str = "", client_id: str = "", client_secret: str = "") -> dict:
    """The key from Google Cloud: the downloaded JSON pasted whole, or the Client ID and secret pasted separately."""
    raw = (raw or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            raise CalendarError("That doesn't look like the Google key file. Copy the Client ID and secret instead.") from None
        if isinstance(data, dict) and data.get("web"):
            raise CalendarError("That key is a 'Web application' key. Make a new one with type Desktop app (step D).")
        info = (data.get("installed") or data) if isinstance(data, dict) else {}
        client_id, client_secret = str(info.get("client_id", "")), str(info.get("client_secret", ""))
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if not client_id.endswith(".apps.googleusercontent.com") or any(c.isspace() for c in client_id):
        raise CalendarError("The Client ID should end in .apps.googleusercontent.com. Copy it again from Google.")
    if len(client_secret) < 10 or any(c.isspace() for c in client_secret):
        raise CalendarError("That Client secret looks cut off. Tap the copy icon next to it in Google and paste again.")
    client = {"client_id": client_id, "client_secret": client_secret}
    storage.save(CLIENT_FILE, client)
    return client


def start_link(uid: str, listen: bool = True) -> str:
    """The Google consent link for this person. With listen, a loopback listener on this machine (alive LINK_TTL)
    catches Google's answer when the browser is on the same computer; otherwise the page takes the pasted address."""
    client = saved_client()
    if not client:
        raise CalendarError("Save the Google key first (step 2).")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    server = None
    if listen:
        try:
            server = HTTPServer(("127.0.0.1", 0), _link_handler())
        except OSError:
            server = None
    redirect = f"http://127.0.0.1:{server.server_port}/" if server else "http://127.0.0.1:1/"
    now = time.time()
    with _pending_lock:
        for k in [k for k, v in _pending.items() if v["until"] < now or v["uid"] == uid]:
            _pending.pop(k, None)                      # one live sign-in per person; a new tap replaces the old one
        _pending[state] = {"uid": uid, "verifier": verifier, "redirect": redirect, "client": client,
                           "until": now + LINK_TTL, "done": None}
    if server:
        threading.Thread(target=_serve_link, args=(server, state), daemon=True).start()
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def _link_handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
            if q.get("state") not in _pending:
                self.send_response(404)
                self.end_headers()
                return
            try:
                _complete(q)
                msg = "<h2>Jarvis is connected to your calendar.</h2><p>You can close this tab.</p>"
            except CalendarError as e:
                msg = f"<h2>Calendar sign-in did not finish.</h2><p>{html.escape(str(e))}</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<!doctype html><meta name=viewport content='width=device-width'>"
                              "<body style='font-family:system-ui;padding:24px'>" + msg).encode())

        def log_message(self, *_):
            pass
    return Handler


def _serve_link(server: HTTPServer, state: str) -> None:
    server.timeout = 1
    try:
        while True:
            p = _pending.get(state)
            if not p or p["done"] is True or p["until"] < time.time():
                break
            server.handle_request()
    finally:
        server.server_close()


def finish_link(uid: str, pasted: str) -> dict:
    """The address Google sent the browser to (pasted from the phone)."""
    text = (pasted or "").strip()
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(text).query).items()} if "?" in text else {}
    if not q.get("state") and not q.get("error"):
        raise CalendarError("Copy the whole address from the top of the page Google opened (it starts with 127.0.0.1).")
    if not q.get("state"):
        with _pending_lock:
            mine = [s for s, p in _pending.items() if p["uid"] == uid]
        if not mine:
            raise CalendarError("That sign-in has expired. Tap Connect again.")
        q["state"] = mine[-1]
    p = _pending.get(q["state"])
    if not p or p["uid"] != uid:
        raise CalendarError("That address is from an older try. Tap Connect again and paste the new address.")
    return _complete(q)


def _complete(q: dict) -> dict:
    """Trade Google's code for the token and save it for the person who started this sign-in."""
    from core import users
    with _pending_lock:
        p = _pending.get(q.get("state", ""))
        if not p or p["until"] < time.time():
            raise CalendarError("That sign-in has expired. Tap Connect again.")
        if p["done"] is True:
            return {"connected": True}
        if p["done"] == "working":
            raise CalendarError("Jarvis is finishing that sign-in right now; give it a second.")
        if p["done"]:
            raise CalendarError(p["done"])
        if q.get("error"):
            p["done"] = ("You tapped Cancel on Google's page. Tap Connect again and choose Allow."
                         if q["error"] == "access_denied" else "Google said: " + str(q["error"]).replace("_", " ") + ".")
            raise CalendarError(p["done"])
        if not q.get("code"):
            raise CalendarError("That address has no code in it. Copy the whole address from the top of the page.")
        if p["uid"] not in users.all_users():          # removed from the household mid-sign-in: never save into someone else
            p["done"] = "That person isn't in the household any more."
            raise CalendarError(p["done"])
        p["done"] = "working"

    def fail(msg: str, retry: bool = False):
        p["done"] = None if retry else msg
        raise CalendarError(msg)

    try:
        r = httpx.post(TOKEN_URL, data={
            "code": q["code"], "client_id": p["client"]["client_id"], "client_secret": p["client"]["client_secret"],
            "redirect_uri": p["redirect"], "grant_type": "authorization_code", "code_verifier": p["verifier"]},
            timeout=TIMEOUT)
    except httpx.HTTPError:
        fail("I couldn't reach Google. Is the internet up? Paste the address again.", retry=True)
    if r.status_code != 200:
        fail("Google refused that sign-in code (it works once and only for a few minutes). Tap Connect again.")
    try:
        tok = _json(r)
    except CalendarError as e:
        fail(str(e))
    if not tok.get("refresh_token"):
        fail("Google gave no long-term pass. Remove Jarvis at myaccount.google.com/permissions and tap Connect again.")
    try:
        life = int(tok.get("expires_in", 0) or 0)
    except (TypeError, ValueError):
        life = 0
    record = {**p["client"], "refresh_token": tok["refresh_token"], "access_token": tok.get("access_token", ""),
              "expires_at": time.time() + life - 60, "scope": tok.get("scope", SCOPE)}
    with users.acting_as(p["uid"]):
        storage.save(TOKEN_FILE, record)
    p["done"] = True
    return {"connected": True}


def link_status(uid: str) -> dict:
    """What the Calendar page shows: connected?, is the family key saved?, and why the last sign-in failed."""
    from core import users
    with users.acting_as(uid):
        on = connected()
    with _pending_lock:
        p = next((p for p in list(_pending.values()) if p["uid"] == uid and p["until"] > time.time()), None)
    err = p["done"] if p and isinstance(p["done"], str) and p["done"] != "working" else ""
    return {"connected": on, "has_client": bool(saved_client()), "error": err,
            "pending": bool(p and p["done"] in (None, "working"))}


def _serve_until(server: HTTPServer, got: dict, timeout: float) -> None:
    server.timeout = 1
    end = time.time() + timeout
    while "code" not in got and "error" not in got and time.time() < end:
        server.handle_request()


def connected() -> bool:
    return bool(storage.load(TOKEN_FILE, {}).get("refresh_token"))


def disconnect() -> bool:
    """Forget the token on this machine (and revoke it at Google, best effort)."""
    rec = storage.load(TOKEN_FILE, {})
    if not rec:
        return False
    try:
        httpx.post("https://oauth2.googleapis.com/revoke", data={"token": rec.get("refresh_token", "")}, timeout=TIMEOUT)
    except httpx.HTTPError:
        pass
    storage.save(TOKEN_FILE, {})
    return True


def _access_token() -> str:
    rec = storage.load(TOKEN_FILE, {})
    if not rec.get("refresh_token"):
        raise CalendarError(NOT_CONNECTED)
    if rec.get("access_token") and rec.get("expires_at", 0) > time.time():
        return rec["access_token"]
    try:
        r = httpx.post(TOKEN_URL, data={"client_id": rec["client_id"], "client_secret": rec["client_secret"],
                                        "refresh_token": rec["refresh_token"], "grant_type": "refresh_token"},
                       timeout=TIMEOUT)
    except httpx.HTTPError:
        raise CalendarError("I couldn't reach Google to open your calendar. Is the internet up?") from None
    if r.status_code in (400, 401):
        raise CalendarError("Google has signed Jarvis out of your calendar. Open Calendar in the Jarvis app and tap Connect again.")
    if r.status_code != 200:
        raise CalendarError(f"Google's sign-in service answered {r.status_code}; try again in a minute.")
    tok = _json(r)
    if not tok.get("access_token"):
        raise CalendarError("Google's sign-in service gave an odd answer; try again in a minute.")
    try:
        life = int(tok.get("expires_in", 3600))
    except (TypeError, ValueError):
        life = 3600
    rec.update(access_token=tok["access_token"], expires_at=time.time() + life - 60)
    storage.save(TOKEN_FILE, rec)
    return rec["access_token"]


def _call(method: str, path: str, **kw) -> dict:
    try:
        r = httpx.request(method, API + path, headers={"Authorization": f"Bearer {_access_token()}"},
                          timeout=TIMEOUT, **kw)
    except httpx.HTTPError:
        raise CalendarError("I couldn't reach Google Calendar. Is the internet up?") from None
    if r.status_code == 401:
        storage.save(TOKEN_FILE, {**storage.load(TOKEN_FILE, {}), "access_token": "", "expires_at": 0})
        raise CalendarError("Google turned down the calendar request; ask me again, and if it repeats, rerun the setup.")
    if r.status_code == 403:
        raise CalendarError("Google says Jarvis isn't allowed to do that on your calendar (403).")
    if r.status_code == 404:
        raise CalendarError("That event isn't on your calendar any more.")
    if r.status_code >= 400:
        raise CalendarError(f"Google Calendar answered {r.status_code}; nothing was changed.")
    return _json(r) if r.content else {}


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise CalendarError("Google sent back something I couldn't read; nothing was changed.")
    return data


# -- times ---------------------------------------------------------------------------------
def _current_offset() -> timedelta:
    delta = ownertime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    return timedelta(minutes=round(delta.total_seconds() / 900) * 15)


def _offset_at(local: datetime) -> timedelta:
    """The owner's UTC offset AT that local wall time (a July question uses EDT even when asked in November)."""
    local = local.replace(tzinfo=None)
    zone = ownertime._zone(ownertime.tz_name())
    if zone is not None:
        return local.replace(tzinfo=zone).utcoffset()
    if ownertime.tz_name() == ownertime.DEFAULT_TZ:  # no tz database: the built-in US Eastern rule
        for hours in (-4, -5):
            if ownertime._us_eastern(local - timedelta(hours=hours)) == local:
                return timedelta(hours=hours)
        return timedelta(hours=-5)
    return _current_offset()


def _rfc3339(dt: datetime) -> str:
    mins = int(_offset_at(dt).total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return dt.replace(microsecond=0, tzinfo=None).isoformat() + f"{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def _to_local(dt: datetime) -> datetime:
    """An aware datetime as the owner's naive wall time, using the offset at THAT moment."""
    zone = ownertime._zone(ownertime.tz_name())
    if zone is not None:
        return dt.astimezone(zone).replace(tzinfo=None)
    utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if ownertime.tz_name() == ownertime.DEFAULT_TZ:
        return ownertime._us_eastern(utc)
    return utc + _current_offset()


def parse_local(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text.strip())
    except (ValueError, AttributeError):
        raise CalendarError(f"I couldn't read the time '{text}'. Use a form like 2026-11-20T15:00.") from None


def _event_start(ev: dict) -> tuple[datetime, bool]:
    s = ev.get("start") or {}
    try:
        if "date" in s:
            return datetime.fromisoformat(s["date"]), True
        dt = datetime.fromisoformat(str(s.get("dateTime", "")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise CalendarError("One of your calendar events has a time I couldn't read.") from None
    return (_to_local(dt) if dt.tzinfo is not None else dt), False


def _event_end(ev: dict) -> datetime | None:
    e = ev.get("end") or {}
    if "dateTime" not in e:
        return None
    return _event_start({"start": e})[0]


# -- calendar operations -------------------------------------------------------------------
def events(start: datetime, end: datetime, query: str = "", limit: int = 50) -> list[dict]:
    params = {"timeMin": _rfc3339(start), "timeMax": _rfc3339(end), "singleEvents": "true",
              "orderBy": "startTime", "maxResults": str(limit), "timeZone": ownertime.tz_name()}
    if query.strip():
        params["q"] = query.strip()
    items = _call("GET", f"/calendars/{CALENDAR}/events", params=params).get("items", [])
    return [ev for ev in items if ev.get("status") != "cancelled"]


def describe(ev: dict, with_day: bool = True) -> str:
    start, all_day = _event_start(ev)
    title = ev.get("summary") or "(no title)"
    if all_day:
        when = start.strftime("%A %B %d").replace(" 0", " ") + ", all day" if with_day else "all day"
        try:
            last = datetime.fromisoformat((ev.get("end") or {}).get("date", "")) - timedelta(days=1)
        except (TypeError, ValueError):
            last = start
        if last.date() > start.date():
            when += " through " + last.strftime("%A %B %d").replace(" 0", " ")
    else:
        clock = start.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
        end = _event_end(ev)
        if end and end.date() == start.date():
            clock += " to " + end.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
        when = (start.strftime("%A %B %d").replace(" 0", " ") + " at " + clock) if with_day else clock
    where = f" at {ev['location']}" if ev.get("location") else ""
    return f"{title}, {when}{where}"


def add(title: str, start: datetime, end: datetime | None = None, all_day: bool = False,
        location: str = "", notes: str = "") -> dict:
    tz = ownertime.tz_name()
    if all_day:  # end = the LAST day, inclusive (Google's end date is exclusive, hence +1)
        day = start.date()
        last = (end.date() if end and end.date() > day else day) + timedelta(days=1)
        body = {"start": {"date": day.isoformat()}, "end": {"date": last.isoformat()}}
    else:
        end = end if end and end > start else start + timedelta(hours=1)
        body = {"start": {"dateTime": start.replace(microsecond=0).isoformat(), "timeZone": tz},
                "end": {"dateTime": end.replace(microsecond=0).isoformat(), "timeZone": tz}}
    body["summary"] = title.strip()
    if location.strip():
        body["location"] = location.strip()
    if notes.strip():
        body["description"] = notes.strip()
    return _call("POST", f"/calendars/{CALENDAR}/events", json=body)


def find(what: str, around: datetime | None = None, days: int = 60) -> list[dict]:
    """Events whose TITLE has all the words, from now (or from the start of the day `around` names) to `days` ahead.
    Google's q also searches notes, places and guests, so its hits are re-checked against the title: a "dentist" in
    another event's notes must never make that event the one we move or delete."""
    start = day_bounds(around.date())[0] if around else ownertime.now()
    end = start + timedelta(days=days)
    words = ([w for w in what.lower().split() if len(w) > 2 and w not in _FILLER]
             or [w for w in what.lower().split() if w not in _OWNERS])   # "my meeting" is the event titled "Meeting"
    if not words:
        return []

    def titled(evs):
        return [ev for ev in evs if all(w in (ev.get("summary") or "").lower() for w in words)]

    hits = titled(events(start, end, query=what))
    if not hits:  # Google's q is word-based ("dentist" misses "Dentists"); fall back to a title scan
        hits = titled(events(start, end, limit=250))
    return hits


_OWNERS = {"the", "my", "our", "his", "her", "their", "your", "a", "an"}
_FILLER = {"the", "my", "our", "his", "her", "their", "your", "and", "for", "with", "event", "appointment",
           "meeting"}


def move(ev: dict, start: datetime, end: datetime | None = None) -> dict:
    tz = ownertime.tz_name()
    old_start, all_day = _event_start(ev)
    if all_day:
        try:
            span = datetime.fromisoformat((ev.get("end") or {}).get("date", "")) - old_start
        except (TypeError, ValueError):
            span = timedelta(days=1)
        body = {"start": {"date": start.date().isoformat()}, "end": {"date": (start + span).date().isoformat()}}
    else:
        if end is None or end <= start:
            old_end = _event_end(ev) or old_start + timedelta(hours=1)
            end = start + (old_end - old_start)
        body = {"start": {"dateTime": start.replace(microsecond=0).isoformat(), "timeZone": tz},
                "end": {"dateTime": end.replace(microsecond=0).isoformat(), "timeZone": tz}}
    return _call("PATCH", f"/calendars/{CALENDAR}/events/{urllib.parse.quote(str(ev.get('id', '')))}", json=body)


def cancel(ev: dict) -> None:
    _call("DELETE", f"/calendars/{CALENDAR}/events/{urllib.parse.quote(str(ev.get('id', '')))}")


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)
