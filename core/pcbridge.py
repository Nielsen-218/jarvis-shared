"""One-hop, authenticated transport to the existing Windows Jarvis, never a shell."""
import contextlib
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from core import users

# Names map to existing tested functions, not arbitrary imports supplied by callers.
TOOLS = {
    "control_computer": "computer", "open_app": "apps", "list_apps": "apps", "close_app": "apps",
    "open_website": "system", "get_system_status": "system", "power": "system",
    "media_control": "media", "volume": "media", "list_programs": "software",
    "install_program": "software", "uninstall_program": "software",
}
_local = threading.local()


class PCError(RuntimeError):
    def __init__(self, message, status=503, unreachable=False):
        super().__init__(message)
        self.status = status
        self.unreachable = unreachable      # the PC did not answer at all (asleep, off the tailnet, Jarvis not running)


ASLEEP = "The PC is asleep or unreachable right now"


def explain(error, goal="", offer=True):
    """What to tell the owner about a PC error, in plain words. A PC that did not answer is 'asleep or unreachable' (not the
    raw transport text), and - for a request that needed it, offer=True - Jarvis OFFERS to queue it as a mission for when it
    is back. Only an offer: nothing is queued until he says yes (tools.missions.start_mission). Anything else keeps its own
    words (a rejected pairing, a missing link setting)."""
    if not getattr(error, "unreachable", False):
        return str(error)
    goal = " ".join(str(goal or "").split())[:120]
    if offer and goal:
        return f'{ASLEEP}, so I could not do that. Want me to queue "{goal}" as a mission for when it is back? Say yes and I will.'
    return ASLEEP + "."


def local_pc():
    return sys.platform == "win32" and os.getenv("JARVIS_ROLE", "all").lower() != "server"


def require_owner(uid=None):
    uid = uid or users.current()
    if users.all_users().get(uid, {}).get("role") != "owner":
        raise PCError("PC control is available to the paired PC owner only.", 403)
    return uid


@contextlib.contextmanager
def user_message(uid, message):
    """Only the real ingress message supplies approval context, never model tool arguments."""
    prev = getattr(_local, "request", None)
    _local.request = (uid, message)
    try:
        yield
    finally:
        _local.request = prev


def original_message():
    request = getattr(_local, "request", None)
    return request[1] if request and request[0] == users.current() else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PCError("The PC address redirected. Check JARVIS_PC_URL; credentials were not forwarded.", 502)


def _address():
    url = os.getenv("JARVIS_PC_URL", "").rstrip("/")
    token = os.getenv("JARVIS_PC_TOKEN", "")
    if not url or not token:
        raise PCError("The PC link needs JARVIS_PC_URL and JARVIS_PC_TOKEN on the laptop.")
    p = urllib.parse.urlsplit(url)
    private = False
    try:
        addr = ipaddress.ip_address(p.hostname or "")
        private = addr.is_private or addr in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        private = p.hostname == "localhost"
    tailnet = bool(p.hostname and p.hostname.endswith(".ts.net"))
    if (p.username or p.password or p.query or p.fragment or p.path not in ("", "/")
            or not ((p.scheme == "https" and (private or tailnet)) or (p.scheme == "http" and private))):
        raise PCError("Use the PC's private Tailscale HTTPS address (or a private LAN address).")
    return url, token


# A request may rightly wait minutes for its answer (a GPU try-on: 420 s), but a PC that's off or asleep doesn't refuse
# the connection over Tailscale, it never answers: the phone sat through the whole wait (190 s for a brain turn) before
# anything else was tried. A bare connect with a short timeout finds out first, and an unreachable PC is then skipped
# for a little while instead of being waited on again by every call.
_CONNECT_SECONDS = 3
_DOWN_SECONDS = 30
_UP_SECONDS = 10
_down = {"until": 0.0, "healthy_until": 0.0}
# A request may rightly wait minutes (GPU renders); a few are quick background pulls that must give up fast.
SLOW_TIMEOUTS = {"/api/pc/intelligence": 190, "/api/pc/cloud": 75, "/api/pc/avatar/tryon": 420,
                 "/api/pc/eng": 200, "/api/pc/eyes": 150, "/api/pc/dress/closet": 60,
                 "/api/pc/thread": 4.0}      # the laptop's background pull of the PC's voice turns (core.thread.PULL_TIMEOUT)


def _answers(url):
    """Does the PC take a connection at all? (Whether its Jarvis is healthy is for the request itself to say.)"""
    now = time.monotonic()
    if now < _down["until"]:
        return False
    # Polling a single remote job used to make a fresh TCP connection before
    # every HTTP request.  A recently successful connection is enough evidence
    # to let the authenticated request itself perform the next health check.
    if now < _down.get("healthy_until", 0.0):
        return True
    p = urllib.parse.urlsplit(url)
    try:
        socket.create_connection((p.hostname, p.port or (443 if p.scheme == "https" else 80)), _CONNECT_SECONDS).close()
        _down["healthy_until"] = now + _UP_SECONDS
        return True
    except OSError:
        _down["until"] = now + _DOWN_SECONDS
        _down["healthy_until"] = 0.0
        return False


# The laptop's own background pulls: not part of anybody's turn, so not in a turn's "relay" seconds (core.turntrace).
_BACKGROUND = ("/api/pc/thread",)
# What the PC's Tailscale proxy (tailscale serve -> 127.0.0.1:8765) answers when Jarvis behind it is not listening: Bad
# Gateway, or Gateway Timeout. No route of Jarvis on the PC answers these itself (every 502 below is raised here, on
# the laptop), so from the PC's address they always mean "the PC is on, its Jarvis is not".
_PROXY_DOWN = (502, 504)


def brain_unreachable():
    """On the laptop: is the PC that does its thinking known not to answer right now? True only while a hop in the last
    seconds found it silent (_answers / request), so nothing is assumed about a PC nobody asked. Never on the PC itself,
    and never on a machine with no PC link (it thinks for itself)."""
    return not local_pc() and bool(os.getenv("JARVIS_PC_URL")) and time.monotonic() < _down["until"]


def request(path, method="GET", body=None):
    """One request to the PC, timed as a "relay" span named for its route (core.turntrace): the laptop's thinking is the
    hop to /api/pc/intelligence, and until 2026-11-19 a phone turn's log said only "other 17.4s". A hop that fails leaves
    its time too ("intelligence failed"), so a wait on a PC that never answered is not invisible."""
    began, ok = time.time(), False
    try:
        out = _request(path, method, body)
        ok = True
        return out
    finally:
        if path.split("?")[0] not in _BACKGROUND:
            try:
                from core import turntrace
                name = (path.split("?")[0].split("/") + ["", "", "", ""])[3] or "pc"
                turntrace.span("relay", name if ok else name + " failed", began, time.time())
            except Exception:  # noqa: BLE001 - timing never breaks a request
                pass


def _request(path, method="GET", body=None):
    started = time.monotonic()
    require_owner()
    if not path.startswith("/api/pc/"):
        raise PCError("Only the PC control API can be relayed.", 400)
    url, token = _address()
    if not _answers(url):
        raise PCError("The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.", unreachable=True)
    req = urllib.request.Request(url + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Jarvis-PC-Hop": "1"})
    try:
        # GPU renders take minutes (a try-on's first run also makes each garment's clothes-only shot);
        # a store search spawns a web-search CLI per piece. Everything else is a quick call.
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=SLOW_TIMEOUTS.get(path, 15)) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise PCError("The PC response exceeded the size limit.", 502)
            _down["healthy_until"] = time.monotonic() + _UP_SECONDS
            try:
                from core import pcledger
                pcledger.latency("pc_network", started, path=path, method=method, outcome="ok")
            except Exception:
                pass
            return data, response.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            # This is the upstream PC pairing, not the phone's pairing with the laptop.
            raise PCError("The PC rejected this pairing. Its owner device token needs to be linked again.", 502) from None
        if error.code in _PROXY_DOWN:
            # The PC took the connection but its Tailscale proxy says Jarvis behind it is not answering (restarting or
            # stopped). Live 2026-11-19: during a PC restart the phone heard "I lost my train of thought" four times.
            _down["healthy_until"] = 0.0
            _down["until"] = time.monotonic() + _DOWN_SECONDS
            raise PCError("The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.",
                          unreachable=True) from None
        try:
            detail = json.loads(error.read(4096)).get("detail", "")
        except (ValueError, UnicodeError):
            detail = ""
        raise PCError(detail or f"The PC returned HTTP {error.code}.", error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        # A quick background pull (4 s) that the PC took a connection for but answered too slowly is a BUSY PC, not a
        # sleeping one: marking it down made the next phone turn skip the PC for 30 s and say it could not be reached
        # (review 2026-11-19). A pull that cannot connect at all is still marked, in _answers above.
        # Either way "it answered a moment ago" no longer stands: the next request looks before it waits (a PC that
        # really went off must not cost a phone turn the whole 190 s of its request; review round 2).
        _down["healthy_until"] = 0.0
        if path.split("?")[0] not in _BACKGROUND:
            _down["until"] = time.monotonic() + _DOWN_SECONDS
        raise PCError("The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.", unreachable=True) from None


# -- the phone's reply audio, made by the PC ---------------------------------------------------------------------------
# Measured 2026-11-19: the laptop's CPU (held at 1.6 GHz, no graphics card) needs 1.4 s to voice 6 words, 4.5 s for 18
# and 10.4 s for 48, and the phone gets no sound until the whole reply is made. The PC that just did the thinking is
# 23 ms away and voices it on its graphics card. This hop is deliberately not request(): it serves every person's reply
# (no PC control is involved, so no owner check here; the PC still accepts only the owner's link), it gives up in
# seconds, and it never marks the PC "down" - a slow voice is not a sleeping PC, and the next turn's thinking must
# still be tried. Its own short memory (_voice_down) keeps a PC that is off from costing every reply a second.
_VOICE_CONNECT_SECONDS = 1.0
_VOICE_SLOW_SECONDS = 10      # after a voice that came too late (PC busy, cold): only this long before asking again
_voice_down = {"until": 0.0}


def _voice_seconds(text):
    """How long the PC may take before the laptop makes the voice itself. Measured 2026-11-19: a warm PC voices 6 to 48
    words in 0.17-0.35 s; the laptop needs 1.3 s for 6 and 8.1-8.5 s for 48. Waiting longer than that is pure loss: right
    after a PC restart its cold voice took the whole old 8 s wait, twice, before the laptop made it anyway (12-13 s)."""
    return min(15.0, 2.0 + len(text) / 400.0)


def speak(text):
    """`text` as MP3 bytes made by the PC. PCError when the PC cannot make it now: the caller makes it itself."""
    url, token = _address()
    now = time.monotonic()
    if now < _down["until"] or now < _voice_down["until"]:
        raise PCError("The PC is unreachable right now.", unreachable=True)
    p = urllib.parse.urlsplit(url)
    if now >= _down.get("healthy_until", 0.0):       # it has not answered in the last seconds: a quick look first
        try:
            socket.create_connection((p.hostname, p.port or (443 if p.scheme == "https" else 80)),
                                     _VOICE_CONNECT_SECONDS).close()
        except OSError:
            _voice_down["until"] = now + _DOWN_SECONDS
            raise PCError("The PC is unreachable right now.", unreachable=True) from None
    req = urllib.request.Request(url + "/api/pc/speak", method="POST", data=json.dumps({"text": text}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Jarvis-PC-Hop": "1"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=_voice_seconds(text)) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            kind = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:          # an older PC without the route, or its voice failed: not "down"
        if error.code in _PROXY_DOWN:                # ...but its Jarvis not running: not asked again for a while
            _voice_down["until"] = time.monotonic() + _DOWN_SECONDS
        raise PCError(f"The PC could not make the voice (HTTP {error.code}).", error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        _voice_down["until"] = time.monotonic() + _VOICE_SLOW_SECONDS
        raise PCError("The PC did not make the voice in time.") from None
    if not data or len(data) > 8 * 1024 * 1024 or not str(kind).startswith("audio/mpeg"):
        raise PCError("The PC returned no audio.", 502)
    return data


def json_request(path, method="GET", body=None):
    data, _ = request(path, method, body)
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise PCError("The PC returned an invalid response; check its Jarvis version.", 502) from None


def submit(name, arguments, request_id):
    return json_request("/api/pc/tasks", "POST", {"name": name, "arguments": arguments,
        "request_id": request_id, "user_message": original_message()})


def run(name, arguments):
    """Poll a job instead of timing out a long HTTP request and accidentally repeating an action."""
    import uuid
    from core import progress

    require_owner()
    job = submit(name, arguments, uuid.uuid4().hex)
    deadline = time.monotonic() + 620
    while job["state"] in ("running", "cancel_requested"):
        progress.step("PC: " + job.get("detail", "working"))
        if time.monotonic() > deadline:
            return [{"type": "text", "text": f"The PC task {job['id']} is still running. Check pc_status; do not repeat it."}]
        # Fast first observations make normal short jobs responsive without
        # changing idempotent submission, cancellation, or the long deadline.
        time.sleep(0.15 if not job.get("polled") else 0.4)
        try:
            job = json_request("/api/pc/tasks/" + job["id"])
            job["polled"] = True
        except PCError:
            return [{"type": "text", "text": f"Lost contact with PC task {job['id']}. It may still be running; check pc_status before retrying."}]
    blocks = job.get("blocks") or [{"type": "text", "text": job.get("error") or job["state"]}]
    # Let a laptop brain and phone both see the real PC's final screenshot.
    from tools.pcview import publish_images
    publish_images(blocks)
    return blocks
