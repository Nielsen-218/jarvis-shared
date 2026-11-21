"""Server for the Jarvis phone app (an installable web app in app/).

It runs inside Jarvis on the PC, listening only on this computer (127.0.0.1).
Phones reach it securely through Tailscale ("tailscale serve"), which adds
HTTPS - required for the microphone, camera, install and notifications.

Each phone is linked to one household member with a pairing code
("Jarvis, pair my phone"); it then gets a device token, and everything it
sends is answered as that person, with their memories, wardrobe and lists.
"""
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from core import hud, notify, progress, storage, users
from core import diag
from core.config import ROOT

APP_DIR = ROOT / "app"
PORT = int(os.getenv("JARVIS_APP_PORT", "8765"))
PAIR_MAX_FAILS, PAIR_WINDOW = 5, 300  # wrong pairing codes allowed per 5 minutes from ONE source address
PAIR_MAX_FAILS_ALL = 15               # ... and in all, from every address together (one guesser cannot lock out the others)
MAX_UPLOAD = 15 * 1024 * 1024  # voice notes and photos from the phone app
CACHE = storage.DATA / "app_cache"  # reply audio, cleaned up after a day
SYNTH_ROOM_S = 8.0   # making one reply's MP3 takes at most about this (core.notice waits it out, then the playback)
VAPID_FILE = "vapid_private.pem"
MAX_EVIDENCE = 1536 * 1024  # 2026-11-10 Mark VI C3: a mission's evidence picture (one window as JPEG)


def _revalidate_pages(static_app):
    """Pages and their scripts/styles: `Cache-Control: no-cache` (the browser re-checks the ETag each time, a cheap 304).
    Without it a long-running window (the desktop Command Center) kept guessing a page was fresh and showed an OLD
    version for up to hours after a deploy. Images, models and audio keep the browser's normal caching."""
    async def app(scope, receive, send):
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        page = path.endswith(("/", ".html", ".js", ".css", ".json", ".webmanifest")) or "." not in path.rsplit("/", 1)[-1]

        async def send_with_header(message):
            if page and message.get("type") == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": headers + [(b"cache-control", b"no-cache")]}
            await send(message)
        await static_app(scope, receive, send_with_header if page else send)
    return app


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_device(token: str) -> tuple[str, dict] | None:
    h = _hash(token)
    for uid, person in users.all_users().items():
        for d in person.get("devices", []):
            if d["token_hash"] == h:
                return uid, d
    return None


def add_device(uid: str, name: str) -> str:
    token = secrets.token_urlsafe(32)
    entry = {"id": uuid.uuid4().hex[:6], "token_hash": _hash(token), "name": name[:40],
             "added": datetime.now().isoformat(timespec="minutes"), "push": None}
    users.mutate_devices(uid, lambda devices: devices + [entry])    # on the freshest merged registry, under the lock
    return token


LINK_BODY_MAX = 32 * 1024     # bytes: a Jarvis Link report or result
LINK_BODY_DEPTH = 10          # levels of nesting a Jarvis Link body may have (a real one has 2)
HEALTH_BODY_MAX = 256 * 1024  # bytes: one chunk of Health Connect data (the app keeps each POST under 200 KB)


def _bracket_depth(raw: bytes) -> int:
    """The deepest [ / { nesting in raw JSON bytes (brackets inside strings are skipped), without parsing it: a
    5000-deep body never reaches the recursive parser."""
    depth = deepest = 0
    in_str = esc = False
    for ch in raw:
        if in_str:
            if esc:
                esc = False
            elif ch == 0x5C:
                esc = True
            elif ch == 0x22:
                in_str = False
        elif ch == 0x22:
            in_str = True
        elif ch in (0x5B, 0x7B):
            depth += 1
            deepest = max(deepest, depth)
        elif ch in (0x5D, 0x7D):
            depth -= 1
    return deepest


def _json_depth(value, level: int = 0) -> int:
    if level > LINK_BODY_DEPTH:
        return level
    if isinstance(value, dict):
        return max((_json_depth(v, level + 1) for v in value.values()), default=level + 1)
    if isinstance(value, list):
        return max((_json_depth(v, level + 1) for v in value), default=level + 1)
    return level


def link_problems() -> list[str]:
    """The PC <-> laptop links fail silently when a pairing is lost (a restored users.json dropped the PC's
    token once, and vault search from the PC quietly 401'd for hours). Report rejected pairings; a machine
    that's simply asleep is normal and not reported."""
    import urllib.error
    import urllib.request
    from core import pcbridge
    from tools import vault
    problems = []
    if pcbridge.local_pc() and vault._remote():
        url, token = vault._remote()
        try:
            urllib.request.urlopen(urllib.request.Request(url + "/api/memory/stats",
                                   headers={"Authorization": f"Bearer {token}"}), timeout=8).close()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                problems.append("Jarvis link: the laptop rejects the PC's token (JARVIS_SERVER_TOKEN), so memory "
                                "search and Forget from the PC don't reach it. Re-link the PC.")
        except Exception as error:
            diag.swallowed("link-check laptop", error)
    elif not pcbridge.local_pc() and os.getenv("JARVIS_PC_URL"):
        owner = next((u for u, p in users.all_users().items() if p.get("role") == "owner"), None)
        try:
            with users.acting_as(owner):
                pcbridge.json_request("/api/pc/status")
        except pcbridge.PCError as e:
            if e.status == 502 and "rejected" in str(e):
                problems.append("Jarvis link: the PC rejects the laptop's token (JARVIS_PC_TOKEN), so PC control, "
                                "GPU try-ons and Eyes from the phone fail. Re-link the laptop.")
        except Exception as error:
            diag.swallowed("link-check pc", error)
    return problems


_LINKS = {"at": 0.0, "value": [], "busy": False}


def link_problems_cached(max_age: float = 300) -> list[str]:
    """The dashboard refreshes every few seconds: never make it wait on a network check."""
    if time.time() - _LINKS["at"] > max_age and not _LINKS["busy"]:
        _LINKS["busy"] = True

        def run():
            try:
                _LINKS["value"] = link_problems()
            except Exception:
                _LINKS["value"] = []
            finally:
                _LINKS["at"], _LINKS["busy"] = time.time(), False
        threading.Thread(target=run, daemon=True, name="linkcheck").start()
    return list(_LINKS["value"])


def pc_page_url(uid: str, page: str) -> str:
    """A link that opens a Jarvis app page in this PC's browser already signed in as `uid`: the PC gets its
    own device token (kept in that person's data folder), passed once in the link's #fragment, which the page
    stores and then removes from the address bar."""
    return f"http://127.0.0.1:{PORT}/{page}#t={pc_browser_token(uid)}"


def pc_browser_token(uid: str = "owner") -> str:
    """This PC's own device token for `uid` (kept in their data folder; re-created if it was unlinked).
    Used by the PC browser pages and by the companion window."""
    with users.acting_as(uid):
        rec = storage.load("pc_browser.json", {})
        token = rec.get("token")
        if not token or not any(d.get("token_hash") == _hash(token) for d in users.get(uid).get("devices", [])):
            token = add_device(uid, "This PC (browser)")
            storage.save("pc_browser.json", {"token": token})
    return token


_GPU_TTL = 4.0                   # nvidia-smi is a process spawn: the Command Center asks every few seconds
_gpu_cache: tuple[float, dict | None] | None = None
_gpu_lock = threading.Lock()
_gpu_clock = time.monotonic


def _gpu_stats() -> dict | None:
    """The graphics card's numbers, or None. Kept for a few seconds (a miss too), so several windows or a fast poll
    cost one nvidia-smi, not one each."""
    global _gpu_cache
    import subprocess
    with _gpu_lock:
        now = _gpu_clock()
        if _gpu_cache is not None and now - _gpu_cache[0] < _GPU_TTL:
            return _gpu_cache[1]
        gpu = None
        try:
            q = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            name, util, used, total, temp = [x.strip() for x in q.stdout.splitlines()[0].split(",")]
            # some drivers print decimals ("50.87"): int() on that used to drop the whole GPU block (seen live 2026-11-12)
            gpu = {"name": name.replace("NVIDIA GeForce ", ""), "util_pct": round(float(util)),
                   "vram_used_gb": round(float(used) / 1024, 1), "vram_gb": round(float(total) / 1024), "temp_c": round(float(temp))}
        except Exception:
            gpu = None
        _gpu_cache = (now, gpu)
        return gpu


def system_stats() -> dict:
    """Live numbers for the PC dashboard: CPU, memory, GPU, the system drive, uptime."""
    import shutil

    import psutil
    out = {"cpu_pct": round(psutil.cpu_percent(interval=0.3)), "mem_pct": round(psutil.virtual_memory().percent),
           "mem_gb": round(psutil.virtual_memory().total / 2**30),
           "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1)}
    try:
        du = shutil.disk_usage(os.getenv("SystemDrive", "C:") + "\\")
        out.update(disk_free_gb=round(du.free / 2**30), disk_pct=round(100 * du.used / du.total))
    except OSError:
        pass
    gpu = _gpu_stats()
    if gpu:
        out["gpu"] = gpu
    return out


def set_push(uid: str, device_id: str, subscription: dict | None) -> None:
    def go(devices):
        for d in devices:
            if d["id"] == device_id:
                d["push"] = subscription
        return devices                    # unchanged or unknown subscription: users._commit writes nothing
    users.mutate_devices(uid, go)


# -- push notifications ----------------------------------------------------------------
def vapid_public_key() -> str:
    """Create the server's push-notification key pair on first use; return the public part."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    path = storage.DATA / VAPID_FILE
    if not path.exists():
        v = Vapid()
        v.generate_keys()
        v.save_key(str(path))
    v = Vapid.from_file(str(path))
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


_EXTRA_RULES = {   # 2026-11-10 Mark VI C3: what a push may carry beyond its text; anything else is dropped
    "url": r"/#mission=[0-9a-f]{12}(&do=(approve|deny))?",
    "tag": r"mission-[0-9a-f]{12}",
    "mission": r"[0-9a-f]{12}",
    "kind": r"approval|done|blocked|failed|unknown",
}


def clean_push_extra(extra) -> dict:
    """Keep only the whitelisted keys whose values match their pattern (the PC relays these through /api/notify, so the
    laptop never trusts them blindly: a push can only ever link to a mission sheet)."""
    import re
    if not isinstance(extra, dict):
        return {}
    return {k: v for k, v in extra.items() if k in _EXTRA_RULES and isinstance(v, str) and re.fullmatch(_EXTRA_RULES[k], v)}


def push(title: str, body: str, uid: str | None, category: str = "jarvis", urgent: bool = False, extra: dict | None = None) -> int:
    """Send a notification to that person's phones (or everyone's), honouring each person's categories and quiet
    hours, and keep it in their notification history. Returns how many phones it reached."""
    from pywebpush import WebPushException, webpush

    extra = clean_push_extra(extra)
    payload = {"title": title, "body": body, "category": category, **extra,
               "tag": extra.get("tag") or f"jarvis-{category}", "url": extra.get("url") or "/#notifications"}
    sent = 0
    targets = [uid] if uid else list(users.all_users())
    for u in targets:
        reached = 0
        if notify.allowed(u, category, urgent):
            for d in users.get(u).get("devices", []):
                if not d.get("push"):
                    continue
                try:
                    webpush(subscription_info=d["push"],
                            data=json.dumps(payload),
                            vapid_private_key=str(storage.DATA / VAPID_FILE),
                            vapid_claims={"sub": "mailto:jarvis@example.com"}, ttl=3600 * (12 if urgent else 4))
                    reached += 1
                except WebPushException as e:
                    if getattr(e, "response", None) is not None and e.response.status_code in (404, 410):
                        set_push(u, d["id"], None)  # the phone unsubscribed
                    else:
                        print(f"[app] push failed: {e}")
        notify.record(title, body, u, category, reached > 0)
        sent += reached
    return sent


def _push_channel(title, body, uid, category="jarvis", urgent=False, extra=None):
    push(title, body, uid, category, urgent, extra)


_push_channel.wants_category = True
_push_channel.wants_extra = True


# Audio made ahead for the one line that has to be said while the PC cannot help: {text: MP3 bytes}. With the PC off the
# laptop voices everything on its own CPU (about 0.75 s per second of speech), and "I can't reach the PC ..." is exactly
# the reply that must not take six more seconds. Made once at start (warm_voice), for the owner's wording.
_ready_audio: dict = {}


def _line_for_a_silent_pc() -> str:
    """The owner's "I can't reach the PC" sentence, as the Brain will word it (core.pchelp.pc_out_of_reach); '' if unknown."""
    try:
        from core import pchelp
        owner = next((uid for uid, person in users.all_users().items() if person.get("role") == "owner"), None)
        if not owner:
            return ""
        with users.acting_as(owner):
            return pchelp.pc_out_of_reach(users.title(owner))
    except Exception:  # noqa: BLE001 - the voice is still warmed with a plain word
        return ""


def warm_voice():
    """Load the reply voice in the background when the PHONE server starts (the laptop). Measured 2026-11-19: each of his
    real phone turns of 16-17 s was the first spoken reply after a restart, with the voice model loading inside the turn
    (about 10 s on the laptop's CPU), and the laptop restarts on every deploy. The PC's voice loop warms its own voice.
    What is voiced to warm it is the PC-off line, kept for when it is needed (_ready_audio).
    JARVIS_VOICE_WARM=off leaves it for the first reply, as before. -> the thread, or None when nothing is warmed."""
    from core import pcbridge
    if pcbridge.local_pc() or os.getenv("JARVIS_VOICE_WARM", "on").lower() == "off":
        return None

    def run():
        began = time.monotonic()
        try:
            from audio import tts
            line = _line_for_a_silent_pc()
            data = tts.mp3_voiced(line or "Ready.")  # an error when no voice said it: silence is never kept as the line
            if line and data:
                _ready_audio.clear()
                _ready_audio[line] = data
            print(f"[app] reply voice ready ({time.monotonic() - began:.1f}s)")
        except Exception as error:  # noqa: BLE001 - the first reply loads it instead, as before
            print(f"[app] voice warm-up failed: {type(error).__name__}: {str(error)[:120]}")

    thread = threading.Thread(target=run, daemon=True, name="voice-warm")
    thread.start()
    return thread


# -- the server -------------------------------------------------------------------------
class AppServer:
    def __init__(self, make_brain, transcriber=None, verify_brain=None):
        self.make_brain = make_brain  # make_brain(uid) -> Brain; one conversation per person
        # verify_brain(uid) -> a Brain of its own for a developer's verification device ('claude-verify'): on the PC
        # make_brain is the voice's own brain_for, and a verify turn must never be part of his conversation (main.py).
        self.verify_brain = verify_brain
        self.brains: dict = {}
        self.on_listen = None         # main.py: wake the microphone (the desktop companion's Talk -> Listen)
        self.on_stop = None           # main.py: stop speaking now (the Command Center's speaker button)
        self.on_mute = None           # main.py: toggle the microphone mute, returns the new state
        self._pair_fails: list[float] = []
        self._pair_fails_by: dict = {}
        self._paircode_lock = threading.Lock()  # POST /api/link/paircode: one writer of pair_codes.json at a time
        self.stt = transcriber
        self._outboxes: dict[int, list] = {}
        self.app = self._build()

    def _brain(self, uid: str, verify: bool = False):
        """That person's Brain. On the PC make_brain is the voice's own brain_for (main.py): typed and spoken turns are
        one conversation, one lock. So the Brain is never changed here: each turn runs gathered (_answer). Only a brain
        without gathered() (an older or fake one) is made gathered for good, as before.
        `verify` (a 'claude-verify' device, the live-verify recipe): its own Brain when verify_brain is given (review
        2026-11-11: on the voice's Brain a verify turn held his spoken turn, took his 'no' as the answer to its question
        and became the referent of his 'close it')."""
        if verify and self.verify_brain is not None:
            key = (uid, "verify")
            if key not in self.brains:
                self.brains[key] = self.verify_brain(uid)
            return self.brains[key]
        if uid not in self.brains:
            b = self.make_brain(uid)
            if not hasattr(b, "gathered"):
                b.live = False   # one gathered reply per message: no "Right away, sir" / progress lines after the result
            self.brains[uid] = b
        return self.brains[uid]

    def start(self) -> None:
        import uvicorn

        CACHE.mkdir(parents=True, exist_ok=True)
        hud.add_media_listener(self._collect_media)
        if os.getenv("JARVIS_PUSH", "on").lower() != "off":  # off on the PC once the server sends them
            notify.add_channel(_push_channel)
        else:   # the PC: the always-on laptop delivers to phones (and records the history)
            notify.add_channel(notify.forward_to_server)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=PORT, log_level="warning")
        threading.Thread(target=uvicorn.Server(config).run, daemon=True, name="app-server").start()
        threading.Thread(target=self._clean_cache, daemon=True).start()
        from core import pcbridge
        if not pcbridge.local_pc():   # 2026-11-10 Mark VI C2: the queue lives on the laptop, so recovery runs there only
            threading.Thread(target=self._recover_missions, daemon=True, name="missions-recover").start()
        warm_voice()
        print(f"[app] phone app server on http://127.0.0.1:{PORT} (share it with 'tailscale serve')")

    # -- helpers ---------------------------------------------------------------------------
    def _collect_media(self, path, caption: str) -> None:
        box = self._outboxes.get(threading.get_ident())
        if box is not None and path and Path(path).exists():
            box.append((str(path), caption))

    def _answer(self, uid: str, content: list, label: str, speak: bool, verify: bool = False) -> dict:
        """Run one request through that person's Jarvis; collect text, images and audio. `verify`: a developer's
        verification device (its own Brain, and never his reply to anything)."""
        brain = self._brain(uid, verify)
        if not verify:
            try:   # on the PC his typed line is his next reply to unprompted news, as a spoken one is (core.notice)
                from core import notice
                notice.heard_reply(uid, label, typed=True)
            except Exception as error:  # noqa: BLE001
                diag.swallowed("notice reply", error)
        # A stop must reach the PC while its original chat request still holds the brain lock.
        import re
        from core import pcbridge

        stop_pc = re.fullmatch(r"(?:hey\s+)?(?:jarvis[,\s]+)?(?:stop|cancel|never mind|nevermind)"
            r"(?:\s+(?:the\s+)?(?:pc|computer)(?:\s+(?:task|control))?)?[.!]?", label.strip(), re.I)
        busy = getattr(brain, "_lock", None) is not None and brain._lock.locked()
        if stop_pc and busy and not pcbridge.local_pc() and users.get(uid).get("role") == "owner":
            with users.acting_as(uid):
                try:
                    stopped = pcbridge.json_request("/api/pc/cancel", "POST", {})
                    reply = stopped.get("detail", "Stop requested for the PC task.")
                    # the phone's own turn (the one it gathered, _answer below), whatever else shares this Brain
                    brain.cancel("", channel="phone") if hasattr(brain, "gathered") else brain.cancel("")
                except pcbridge.PCError as error:
                    reply = pcbridge.explain(error, offer=False)      # asleep: say so plainly, not the raw transport text
            return {"reply": reply, "images": [], "audio": None}
        if progress.is_status_question(label) and getattr(brain, "_lock", None) is not None and brain._lock.locked():
            # Jarvis is mid-task for them: answer "what's the hold up?" right away, without waiting or cancelling.
            from audio.language import detect

            reply = progress.report({"ht": "ht", "fr": "fr"}.get(detect(label), "en"))
            return {"heard": label, "reply": reply, "images": [], "audio": self._speak(reply) if speak else None}
        name = users.get(uid).get("name") or ""
        hud.line("you", f"📱 {name + ': ' if name else ''}{label}")
        me = threading.get_ident()
        self._outboxes[me] = []
        said: list[str] = []
        gathered = getattr(brain, "gathered", None)
        # One gathered reply per message (no "Right away, sir" / progress lines after the result), named for the thread
        # ledger (core.thread): the laptop serves the phone; on the PC it is the companion's typed chat.
        turn = gathered("phone" if not pcbridge.local_pc() else "typed") if gathered else contextlib.nullcontext()
        board = None
        with users.acting_as(uid), pcbridge.user_message(uid, label), turn:
            try:
                from tools import study
                study.take_notice()                       # a whiteboard left from an earlier turn is not this turn's
            except Exception as error:  # noqa: BLE001
                diag.swallowed("study notice", error)
            brain.ask(content, said.append)
            try:
                board = study.take_notice()               # an explanation given this turn: which whiteboard to show
            except Exception as error:  # noqa: BLE001
                diag.swallowed("study notice", error)
        said = [s for s in said if not s.strip().startswith("[ignore]")]
        reply = " ".join(said)
        hud.line("jarvis", f"📱 {reply[:200]}")
        try:  # both sides of the phone conversation go into the person's 24-hour Daily Awareness log
            from core import awareness
            with users.acting_as(uid):
                if label and not label.startswith("("):
                    awareness.add(name or "You", label, "jarvis")
                if reply:
                    awareness.add("Jarvis", reply, "jarvis")
        except Exception as error:
            diag.swallowed("awareness log", error)
        images = [{"url": self._file_url(uid, p), "caption": c} for p, c in self._outboxes.pop(me, [])]
        audio = self._speak(reply) if speak and reply else None
        out = {"reply": reply, "images": [i for i in images if i["url"]], "audio": audio}
        if board and reply:
            out["board"] = {k: board[k] for k in ("topic", "rung", "label", "page", "steps")}
        return out

    def _file_url(self, uid: str, path: str) -> str | None:
        p = Path(path).resolve()
        for base in (storage.DATA / "users" / uid, storage.DATA / "household"):
            try:
                rel = p.relative_to(base.resolve())
                return f"/api/file/{'u' if 'users' in base.parts else 'h'}/{rel.as_posix()}"
            except ValueError:
                continue
        return None

    def _speak(self, text: str) -> str | None:
        """Reply audio: each sentence in the right voice for its language, as one MP3. The desktop companion plays it in
        its own window, which the voice loop never sees: core.notice says nothing unprompted while it is made and played.
        On the laptop the PC makes it when it is awake (core.pcbridge.speak: its graphics card voices in a fraction of
        the laptop's 1.4-10 s); whenever the PC cannot, the laptop makes it itself as before. JARVIS_PHONE_VOICE=local
        keeps it on the laptop."""
        from core import notice, pcbridge, turntrace
        notice.voiced_elsewhere(text, extra=SYNTH_ROOM_S)      # being made, then played
        data = _ready_audio.get(text)                          # the PC-off line: made at start, said at once
        if data is not None:
            turntrace.span("voice", "ready", time.time(), time.time())
        elif (not pcbridge.local_pc() and os.getenv("JARVIS_PC_URL")
                and os.getenv("JARVIS_PHONE_VOICE", "pc").lower() != "local"):
            began = time.time()
            try:
                data = pcbridge.speak(text)
                turntrace.span("voice", "pc", began, time.time())
            except pcbridge.PCError:
                if time.time() - began >= 0.05:                # a refusal known at once (asleep, remembered) cost nothing
                    turntrace.span("voice", "pc failed", began, time.time())
            except Exception as error:  # noqa: BLE001 - never the reply's voice for a fault in the shortcut
                print(f"[app] PC voice failed: {type(error).__name__}")
        if data is None:
            began = time.time()
            try:
                from audio.tts import mp3_for

                data = mp3_for(text)
            except Exception as e:
                turntrace.span("voice", "failed", began, time.time())
                print(f"[app] voice failed: {e}")
                return None
            turntrace.span("voice", "local", began, time.time())   # the turn's ledger: how long the audio took to make
        notice.voiced_elsewhere(text)                          # made: played from now
        name = f"{uuid.uuid4().hex}.mp3"
        (CACHE / name).write_bytes(data)
        return f"/api/audio/{name}"

    # -- dashboard data (cached so a TV refreshing every few seconds costs nothing) -------------
    _cache: dict = {}

    def _cached(self, key: str, seconds: int, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
        try:
            value = fn()
        except Exception as e:
            value = {"error": str(e)[:200]}
        self._cache[key] = (time.time(), value)
        return value

    @staticmethod
    def _homelab() -> dict:
        from tools import homelab

        return homelab.snapshot()

    @staticmethod
    def _weather() -> dict | None:
        city = os.getenv("JARVIS_HOME_CITY")
        if not city:
            return None
        from tools.weather import get_weather

        lines = get_weather(city, 1).splitlines()
        return {"now": lines[0], "today": lines[1] if len(lines) > 1 else ""}

    @staticmethod
    def _playing() -> list:
        from tools import homelab

        if not os.getenv("JELLYFIN_API_KEY"):
            return []
        sessions = homelab._jf("/Sessions") or []
        return [{"user": s.get("UserName"), "title": s["NowPlayingItem"].get("Name"),
                 "series": s["NowPlayingItem"].get("SeriesName")}
                for s in sessions if s.get("NowPlayingItem")]

    @staticmethod
    def _today(uid: str) -> list:
        from tools import proactive

        today = datetime.now().date().isoformat()
        with users.acting_as(uid):
            items = proactive._my_reminders()
        return sorted(f"{r['due'][11:16]} {r['text']}" for r in items if r["due"].startswith(today))

    def _clean_cache(self) -> None:
        while True:
            cutoff = time.time() - 86400
            for f in CACHE.glob("*.mp3"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            time.sleep(3600)

    def _recover_missions(self) -> None:
        """2026-11-10 Mark VI C2: every minute, a running mission whose worker has gone silent for 10 minutes (the PC
        crashed, lost the network) is queued once more or marked unknown. By silence only, so a mission the PC is still
        working on (it heartbeats before each step) is never taken away from it."""
        from core import missions
        while True:
            try:
                missions.recover_and_report()
            except Exception as error:  # noqa: BLE001
                diag.swallowed("missions recover", error)
            try:
                missions.backup_daily()                      # a copy a day, the last seven kept (cross-device audit 9)
            except Exception as error:  # noqa: BLE001
                diag.swallowed("missions backup", error)
            time.sleep(60)

    # -- routes ----------------------------------------------------------------------------
    def _build(self):
        from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
        from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
        from fastapi.staticfiles import StaticFiles
        from starlette.middleware.gzip import GZipMiddleware

        from core import pcbridge  # owner-guard + PC relay used by the avatar/studio routes below

        app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None, openapi_url=None)
        server = self

        @app.exception_handler(pcbridge.PCError)
        async def pc_error(request, error):
            # Owner-only routes call pcbridge.require_owner directly; without this a household
            # member's phone got an unhandled 500 instead of the intended 403/502/503.
            return JSONResponse(status_code=error.status, content={"detail": str(error)})

        def auth(authorization: str = Header(default=""), request: Request = None) -> tuple[str, dict]:
            token = authorization.removeprefix("Bearer ").strip() or request.query_params.get("t", "")
            found = find_device(token) if token else None
            if not found:
                raise HTTPException(401, "Not paired")
            return found

        from core.teaching_api import install as install_teaching
        install_teaching(app, auth)

        from core.playhouse.api import install as install_playhouse
        install_playhouse(app, auth)

        @app.post("/api/pair")
        async def pair(body: dict, request: Request):
            from tools import voice

            # Guessing is limited per source address (a wrong-code streak from one device no longer locks the owner's phone
            # out), and in all: after PAIR_MAX_FAILS wrong codes from one address, or PAIR_MAX_FAILS_ALL from everywhere, in
            # PAIR_WINDOW seconds, pairing pauses for PAIR_WINDOW. Behind a proxy every request shares one address, which
            # is the old global behaviour.
            now = time.monotonic()
            source = (request.client.host if request.client else "") or "?"
            server._pair_fails = [t for t in server._pair_fails if now - t < PAIR_WINDOW]
            mine = server._pair_fails_by[source] = [t for t in server._pair_fails_by.get(source, []) if now - t < PAIR_WINDOW]
            if len(mine) >= PAIR_MAX_FAILS or len(server._pair_fails) >= PAIR_MAX_FAILS_ALL:
                raise HTTPException(429, "Too many wrong codes. Wait a few minutes, then ask Jarvis for a new one.")
            code = str(body.get("code", "")).strip()[:12]
            uid = voice.pair_codes.pop(code, None)
            if uid is None:  # codes made with "python manage.py pair <name>"
                from manage import take_file_code

                uid = take_file_code(code)
            if uid is None:
                server._pair_fails.append(now)
                mine.append(now)
                if len(server._pair_fails_by) > 64:               # forget addresses with nothing recent
                    server._pair_fails_by = {k: v for k, v in server._pair_fails_by.items() if v}
                await asyncio.sleep(1)  # slow down guessing without freezing the server for everyone
                raise HTTPException(403, "That code isn't valid. Ask Jarvis for a new one.")
            token = add_device(uid, str(body.get("device", "phone")))
            person = users.get(uid)
            hud.line("sys", f"App linked: {person.get('name') or 'owner'}'s {body.get('device', 'phone')}")
            return {"token": token, "name": person.get("name", ""), "title": users.title(uid)}

        @app.get("/api/me")
        def me(who=Depends(auth)):
            uid, device = who
            person = users.get(uid)
            from core import profile
            return {"name": person.get("name", ""), "title": users.title(uid), "device": device["id"],
                    "push": bool(device.get("push")), "vapid": vapid_public_key(),
                    "photo": profile.photo_data_url(uid), "tagline": profile.tagline(uid)}

        @app.post("/api/me/photo")
        def me_photo(request: Request, file: UploadFile = File(...), who=Depends(auth)):
            """The owner's profile picture: any JPEG, PNG or WebP up to 8 MB, cropped to a square."""
            from core import profile
            owner_only(who[0])
            try:
                too_big = int(request.headers.get("content-length") or 0) > profile.MAX_UPLOAD + 64 * 1024
            except ValueError:
                too_big = False
            if too_big:                      # refused before the body is read into memory
                raise HTTPException(413, "That picture is too big (keep it under 8 MB).")
            try:
                profile.save_photo(who[0], file.file.read(profile.MAX_UPLOAD + 1))
            except profile.ProfileError as e:
                raise HTTPException(e.status, str(e)) from None
            return {"ok": True, "photo": profile.photo_data_url(who[0])}

        @app.post("/api/me/tagline")
        def me_tagline(body: dict, who=Depends(auth)):
            from core import profile
            owner_only(who[0])
            return {"ok": True, "tagline": profile.set_tagline(who[0], body.get("tagline"))}

        @app.get("/api/app-icon")
        def app_icon(name: str = "", who=Depends(auth)):
            """The installed program's own icon as a PNG (cached); 404 when the app or its icon isn't available."""
            from core import applogos
            owner_only(who[0])
            path = applogos.icon_path(name)
            if path is None:
                raise HTTPException(404)
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

        @app.get("/api/launcher")
        def launcher_get(who=Depends(auth)):
            """Quick Launch: the pinned apps and the installed programs one can pin."""
            from core import applogos
            owner_only(who[0])
            pinned = applogos.pinned(who[0])
            return {"pinned": pinned, "max": applogos.MAX_PINNED,
                    "apps": [n for n in applogos.catalogue_names() if n not in pinned]}

        @app.post("/api/launcher/pin")
        def launcher_pin(body: dict, who=Depends(auth)):
            from core import applogos
            owner_only(who[0])
            try:
                return {"ok": True, "pinned": applogos.pin(who[0], body.get("name"))}
            except ValueError as e:
                raise HTTPException(400, str(e)) from None

        @app.post("/api/launcher/unpin")
        def launcher_unpin(body: dict, who=Depends(auth)):
            from core import applogos
            owner_only(who[0])
            return {"ok": True, "pinned": applogos.unpin(who[0], body.get("name"))}

        # -- House cameras (owner only; the recorder and camgate run on the home lab Pi, see core/homecam.py) -----------
        try:                                               # alerts + follow mode: the phone server only, once
            from core import homecam as _homecam
            _homecam.start_monitor()
        except Exception as e:  # noqa: BLE001
            print(f"[house] monitor not started: {type(e).__name__}: {e}")
        def house_call(fn, *args):
            from core import homecam
            try:
                return fn(*args)
            except homecam.CameraError as e:
                raise HTTPException(e.status, str(e)) from None

        @app.get("/api/house/cameras")
        def house_cameras(who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            return house_call(homecam.summary, True)

        @app.get("/api/house/frame/{cam}")
        def house_frame(cam: str, who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            return Response(house_call(homecam.frame, cam), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

        @app.get("/api/house/live/{cam}")
        def house_live(cam: str, video: int = 0, who=Depends(auth)):
            """The live stream as fragmented MP4, proxied from the Pi so the camera never faces anything but the LAN.
            ?video=1: picture only (the instant player, whose sound comes over WebRTC)."""
            from core import homecam
            camera_viewer(who[0])
            from starlette.background import BackgroundTask
            stream = house_call(homecam.live, cam, bool(video))   # holds a viewer place; closed however the response ends
            return StreamingResponse(stream, media_type="video/mp4", headers={"Cache-Control": "no-store"},
                                     background=BackgroundTask(stream.close))

        @app.post("/api/house/webrtc/{cam}")
        async def house_webrtc(cam: str, request: Request, who=Depends(auth)):
            """Near-instant live view: WebRTC offer in (SDP text), go2rtc's answer out."""
            from starlette.concurrency import run_in_threadpool
            from core import homecam
            camera_viewer(who[0])
            offer = (await request.body())[:60000].decode("utf-8", "replace")
            answer = await run_in_threadpool(house_call, homecam.webrtc_answer, cam, offer)
            return Response(answer, media_type="application/sdp", headers={"Cache-Control": "no-store"})

        @app.get("/api/house/mjpeg/{cam}")
        def house_mjpeg(cam: str, hd: int = 0, who=Depends(auth)):
            """Live view as motion-JPEG: iPhones (they can't play the fragmented-MP4 live stream) and, with ?hd=1, the
            "steady" view in 1280x720 when the camera's main stream is too damaged for the browser's decoders."""
            from starlette.background import BackgroundTask
            from core import homecam
            camera_viewer(who[0])
            stream = house_call(homecam.live_mjpeg, cam, bool(hd))
            return StreamingResponse(stream, media_type=stream.ctype, headers={"Cache-Control": "no-store"},
                                     background=BackgroundTask(stream.close))

        @app.get("/api/house/recordings/{cam}")
        def house_recordings(cam: str, limit: int = 60, who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            return {"items": house_call(homecam.recordings, cam, limit)}

        @app.get("/api/house/recording/{cam}/{day}/{file}")
        def house_recording(cam: str, day: str, file: str, request: Request, download: int = 0, who=Depends(auth)):
            """One recorded segment; ?download=1 saves it as <camera>-<day>-<time>.mp4 instead of playing it."""
            from core import homecam
            camera_viewer(who[0])
            code, headers, body = house_call(homecam.recording, cam, day, file, request.headers.get("range", ""))
            name = headers.pop("X-Name")                  # built from the validated, normalised names
            if download:
                headers["Content-Disposition"] = f'attachment; filename="{name}"'
            return StreamingResponse(body, status_code=code, headers=headers, media_type="video/mp4")

        @app.post("/api/house/ptz/{cam}")
        def house_ptz(cam: str, body: dict, who=Depends(auth)):
            """Turn the camera (left/right/up/down/diagonals, t2p pan tilt, home) or switch day/night."""
            from core import homecam
            camera_viewer(who[0])
            cmd = str(body.get("cmd", ""))
            if cmd == "aim":                               # tap-to-aim: a point of the picture, -0.5..0.5 from the centre
                return house_call(homecam.aim, cam, body.get("dx"), body.get("dy"))
            if cmd in homecam._SWAP_H or cmd in homecam._SWAP_V:
                return house_call(homecam.move, cam, cmd)   # arrows as he sees them (flip setting applied)
            return house_call(homecam.ptz, cam, cmd, body.get("pan"), body.get("tilt"))

        @app.get("/api/house/presets/{cam}")
        def house_presets(cam: str, who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            return {"presets": house_call(homecam.presets, cam)}

        @app.post("/api/house/presets/{cam}")
        def house_preset_action(cam: str, body: dict, who=Depends(auth)):
            """{action: save|go|delete, name}: save where the camera points now, go back to it, or forget it."""
            from core import homecam
            camera_viewer(who[0])
            action, name = str(body.get("action", "")), str(body.get("name", ""))[:24]
            if action == "save":
                return {"presets": house_call(homecam.save_preset, cam, name)}
            if action == "delete":
                return {"presets": house_call(homecam.delete_preset, cam, name)}
            if action == "go":
                return house_call(homecam.go_preset, cam, name)
            raise HTTPException(400, "action must be save, go or delete")

        @app.post("/api/house/talk/{cam}")
        async def house_talk(cam: str, request: Request, who=Depends(auth)):
            """Push-to-talk: the body is his recording (webm/ogg/mp4); it comes out of the camera's speaker."""
            from starlette.concurrency import run_in_threadpool
            from core import homecam
            camera_viewer(who[0])
            data = await request.body()
            return await run_in_threadpool(house_call, homecam.talk, cam, data)

        @app.post("/api/house/announce/{cam}")
        def house_announce(cam: str, body: dict, who=Depends(auth)):
            """Jarvis says the text out of the camera's speaker, in his voice."""
            from core import homecam
            camera_viewer(who[0])
            return house_call(homecam.announce, cam, str(body.get("text", ""))[:300])

        @app.get("/api/house/settings")
        def house_settings(who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            s = homecam.settings()
            s.pop("privacy_return", None)
            return dict(s, alertable=list(homecam.ALERTABLE))

        @app.post("/api/house/settings")
        def house_settings_set(body: dict, who=Depends(auth)):
            """{alerts: {on, labels, night}} | {follow: {cam: bool}} | {flip: {cam: {pan, tilt}}}."""
            from core import homecam
            camera_viewer(who[0])
            s = house_call(homecam.update_settings, body)
            s.pop("privacy_return", None)
            return s

        @app.post("/api/house/privacy/{cam}")
        def house_privacy(cam: str, body: dict, who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            return house_call(homecam.set_privacy, cam, bool(body.get("on")))

        @app.get("/api/house/events/{cam}")
        def house_events(cam: str, limit: int = 40, who=Depends(auth)):
            """What the detector saw (people, pets, cars), newest first, each tied to its spot in the recordings."""
            from core import homecam
            camera_viewer(who[0])
            return {"items": house_call(homecam.events, cam, limit)}

        @app.get("/api/house/event/{eid}.jpg")
        def house_event_snapshot(eid: str, who=Depends(auth)):
            from core import homecam
            camera_viewer(who[0])
            return Response(house_call(homecam.event_snapshot, eid), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=60"})

        # -- World Intelligence (PC Hub only; the globe is the local God's Eye View service, see core/worldintel.py) -----
        def world_pc_only(uid: str) -> None:
            owner_only(uid)
            if os.getenv("JARVIS_ROLE") == "server":
                raise HTTPException(404, "World Intelligence runs on the PC Hub.")

        @app.get("/api/world/layers")
        def world_layers(who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            return {"layers": worldintel.LAYERS, "status": worldintel.feeds_status(), "gev_port": worldintel.GEV_PORT}

        @app.post("/api/world/service")
        def world_service(body: dict, who=Depends(auth)):
            """The workspace opening starts the loopback globe service; closing stops it (nothing heavy stays running)."""
            from core import worldintel
            world_pc_only(who[0])
            return worldintel.gev_start() if body.get("on") else worldintel.gev_stop()

        @app.get("/api/world/livecams")
        def world_livecams(who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            return {"items": worldintel.livecam_list()}

        @app.get("/api/world/livecam/{cam_id}")
        def world_livecam(cam_id: str, who=Depends(auth)):
            """The landmark cam's CURRENT live stream (YouTube ids change when a broadcast restarts)."""
            from core import worldintel
            world_pc_only(who[0])
            return worldintel.livecam(cam_id[:40])

        @app.post("/api/world/full-app")
        def world_full_app(who=Depends(auth)):
            """The full local God's Eye View (street level, 3D cities, CCTV, radio) in its own browser window."""
            from core import worldintel
            world_pc_only(who[0])
            return worldintel.open_full_app()

        @app.post("/api/world/live")
        def world_live(body: dict, who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            worldintel.set_live(bool(body.get("on")))
            return {"ok": True, "live": worldintel.live_enabled()}

        @app.get("/api/world/feed/{name}")
        def world_feed(name: str, hours: int = 24, min_mag: float = 2.5, who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            if name == "earthquakes":
                return worldintel.earthquakes(max(1, min(hours, 168)), max(0.0, min(min_mag, 9.0)))
            if name == "alerts":
                return worldintel.alerts()
            if name == "stormwatch":       # core/stormwatch: the owner's pinned places and what is active near them now
                if not worldintel.live_enabled():
                    return {"ok": False, "items": [], "error": "Live data loading is switched off.", "fetched": None}
                from core import protocols, stormwatch
                proto = protocols.find("storm watch")
                places = protocols.storm_places(proto) if proto else []
                return stormwatch.globe_feed(places or list(stormwatch.PLACES))
            raise HTTPException(404, "No such feed.")

        @app.get("/api/world/search")
        def world_search(q: str = "", who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            return worldintel.search_place(q)

        @app.post("/api/world/context")
        def world_context_set(body: dict, who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            worldintel.set_context(body)
            return {"ok": True}

        @app.get("/api/world/actions")
        def world_actions(who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            return {"actions": worldintel.pending_actions()}

        @app.post("/api/world/ack")
        def world_ack(body: dict, who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            return {"ok": worldintel.ack_action(str(body.get("id", ""))[:20], bool(body.get("ok")), str(body.get("detail", "")))}

        @app.get("/api/world/watchlist")
        def world_watch_get(who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            return {"items": worldintel.watchlist()}

        @app.post("/api/world/watchlist")
        def world_watch_post(body: dict, who=Depends(auth)):
            from core import worldintel
            world_pc_only(who[0])
            try:
                return {"items": worldintel.watch_remove(str(body.get("remove"))) if body.get("remove") else worldintel.watch_add(body)}
            except ValueError as e:
                raise HTTPException(400, str(e)) from None

        @app.post("/api/chat")
        def chat(body: dict, who=Depends(auth)):
            uid, _ = who
            text = str(body.get("text", "")).strip()
            image = body.get("image")  # data URL, optional
            content = []
            if len(text) > 8000 or (image and len(image) > MAX_UPLOAD * 4 // 3 + 100):
                raise HTTPException(413, "That message or photo is too big.")
            if image and image.startswith("data:image/"):
                media, data = image[5:].split(";base64,", 1)
                content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
            if not text and not content:
                raise HTTPException(400, "Empty message")
            # The open globe's selection reaches the brain as its own <map-context> block (brain/llm.py), never as words in
            # front of his: a prefix here hid "take me to Haiti" from the fixed World Intelligence phrases (live, 2026-11-15).
            content.append({"type": "text", "text": "[Sent from their Jarvis phone app. Keep replies "
                                                    "conversational; no markdown.] " + (text or "(photo)")})
            from core import gateway, inputwatch, turntrace
            device = who[1] if len(who) > 1 and isinstance(who[1], dict) else {}
            test = str(device.get("name", "")).startswith("claude-verify")   # a developer's verification device
            if not test:
                inputwatch.note_turn()      # the owner is here (practice steps aside at once)
            started, t0 = time.time(), time.monotonic()
            gateway.TEST_TURN.on = test
            try:
                out = server._answer(uid, content, text or "(photo)", bool(body.get("speak", True)), verify=test)
            finally:
                gateway.TEST_TURN.on = False
            took = time.monotonic() - t0
            if text and isinstance(out, dict):    # "show me my house" from the phone: a live-view link and a current frame
                from core import homecam
                with users.acting_as(uid):
                    out.update(homecam.chat_extras(text))
            print(f"[timing] phone turn {took:.1f}s: " + turntrace.record(
                "phone", text, started, took, took, marks={"total": (started, started + took)}, test=test,
                device=str(device.get("id", ""))))
            return out

        # -- the desktop companion's menu (Living Presence Engine) ---------------------------------------------
        @app.get("/api/skills")
        def skills(who=Depends(auth)):
            """Every registered skill, as his Skills browser shows it (core/skills_catalog.py)."""
            from core import skills_catalog
            return skills_catalog.catalog()

        @app.post("/api/skills/run")
        def run_skill(body: dict, who=Depends(auth)):
            """Run one skill picked in his menu, through tools/registry.run like the brain does."""
            uid, _ = who
            if users.get(uid).get("role") != "owner":
                raise HTTPException(403, "Skills from the companion's menu are the owner's.")
            from core import skills_catalog
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            return skills_catalog.run(uid, str(body.get("name", ""))[:64], args)

        @app.post("/api/skills/explain")
        def explain_skill(body: dict, who=Depends(auth)):
            """Put a skill's result into a sentence or two, in his voice. The result is data: no tools run."""
            uid, _ = who
            from core import skills_catalog
            title = str(body.get("title") or body.get("name") or "that skill")[:80]
            text = str(body.get("text", ""))[:6000]
            if not text.strip():
                raise HTTPException(400, "Nothing to explain.")
            said: list[str] = []
            brain = server._brain(uid)
            explain = getattr(brain, "explain_result", None)
            if explain is not None:
                with users.acting_as(uid):
                    try:
                        explain(title, text, said.append)
                    except Exception as e:
                        print(f"[app] explain failed: {type(e).__name__}: {e}")
            reply = " ".join(s.strip() for s in said if s.strip()) or skills_catalog.plain_summary(text)
            return {"reply": reply, "audio": server._speak(reply)}

        @app.post("/api/companion/listen")
        def companion_listen(who=Depends(auth)):
            """His Talk -> Listen: the microphone opens, exactly like the wake word."""
            uid, _ = who
            if users.get(uid).get("role") != "owner":
                raise HTTPException(403, "Owner only.")
            if server.on_listen is None:
                return {"ok": False, "detail": "The microphone isn't running on this computer."}
            server.on_listen()
            return {"ok": True}

        def _owner(who):
            if users.get(who[0]).get("role") != "owner":
                raise HTTPException(403, "Owner only.")

        @app.post("/api/companion/stop")
        def companion_stop(who=Depends(auth)):
            """The Command Center's speaker button: stop talking now."""
            _owner(who)
            if server.on_stop is None:
                return {"ok": False, "detail": "The voice isn't running on this computer."}
            server.on_stop()
            return {"ok": True}

        @app.post("/api/companion/mute")
        def companion_mute(who=Depends(auth)):
            """Toggle the microphone mute (the same switch as the tray)."""
            _owner(who)
            if server.on_mute is None:
                return {"ok": False, "detail": "The microphone isn't running on this computer."}
            return {"ok": True, "muted": bool(server.on_mute())}

        @app.get("/api/companion/visible")
        def companion_visible(who=Depends(auth)):
            _owner(who)
            return {"visible": hud.companion_visible(), "running": hud.running() and hud.mode_name() == "companion"}

        @app.post("/api/companion/visible")
        def companion_set_visible(body: dict, who=Depends(auth)):
            """Call the desktop companion onto the screen, or dismiss him; remembered across restarts."""
            _owner(who)
            done = hud.set_companion_visible(bool(body.get("on")))
            out = {"ok": done, "visible": hud.companion_visible()}
            if not done:
                out["detail"] = "The desktop companion isn't running on this computer."
            return out

        @app.post("/api/say")
        def say_line(body: dict, who=Depends(auth)):
            """Jarvis's voice for one short line (the PC companion's quips and workout cues)."""
            text = str(body.get("text", "")).strip()
            if not text or len(text) > 400:
                raise HTTPException(400, "Say something short.")
            return {"audio": server._speak(text)}

        @app.get("/api/core/scorecard")
        def core_scorecard(who=Depends(auth)):
            """The core scorecard (core/scorecard.py): his everyday commands done right the first time, per day, plus the
            frozen replay's last score. Owner only: it quotes his own lines."""
            uid, _ = who
            if users.get(uid).get("role") != "owner":
                raise HTTPException(403, "Owner only.")
            from core import scorecard
            return scorecard.daily(days=7)

        @app.get("/api/corrections")
        def corrections_queue(who=Depends(auth)):
            """Today's few real lines for the owner to judge (core/corrections.py). Owner only: it quotes his own lines."""
            _owner(who)
            from core import corrections
            return corrections.queue(who[0])

        @app.post("/api/corrections")
        def corrections_record(body: dict, who=Depends(auth)):
            """His verdict on one line: right / wrong (+ what he meant) / skip. Stored in his own folder; nothing runs."""
            _owner(who)
            from core import corrections
            try:
                return corrections.record(who[0], body)
            except ValueError as error:
                raise HTTPException(400, str(error))

        @app.get("/api/system")
        def system(who=Depends(auth)):
            uid, _ = who
            if users.get(uid).get("role") != "owner":
                raise HTTPException(403, "Owner only.")
            stats = dict(server._cached("system", 4, system_stats))
            try:
                from core import watchdog
                crashes = [r for r in watchdog.load_crashes() if r.get("restarted")]
                if crashes:
                    stats["last_crash"] = {"ts": crashes[-1]["ts"], "at": watchdog.clock(crashes[-1]["ts"]),
                                           "cause": crashes[-1].get("cause", ""), "code": crashes[-1].get("code", "")}
            except Exception:
                pass
            return stats

        @app.post("/api/eyes")
        def eyes(body: dict, who=Depends(auth)):
            """Live 'eyes': one or more captured frames (a photo, or frames sampled from a short
            video) plus an optional question, sent to Jarvis's vision."""
            uid, _ = who
            text = str(body.get("text", "")).strip()
            frames = body.get("images") if isinstance(body.get("images"), list) else []
            frames = [f for f in frames if isinstance(f, str) and f.startswith("data:image/")][:8]
            if not frames:
                raise HTTPException(400, "Send at least one captured frame.")
            if sum(len(f) for f in frames) > MAX_UPLOAD * 8 or len(text) > 8000:
                raise HTTPException(413, "That capture is too big.")
            speak = bool(body.get("speak", True))
            # Free paths first: a local vision model on this host, else (the laptop has none) the PC's
            # GPU vision over the private relay. The paid brain is only the last resort.
            from core import vision
            system = ("You are Jarvis looking through the owner's phone camera. Answer briefly and "
                      "naturally in one or two sentences, no markdown; if they asked something, answer it.")
            reply = None
            if vision.available():
                try:
                    reply = vision.describe(frames, text or "What am I looking at?", system=system)
                except Exception:
                    reply = None
            elif not pcbridge.local_pc() and users.get(uid).get("role") == "owner":
                try:
                    with users.acting_as(uid):
                        reply = pcbridge.json_request("/api/pc/eyes", "POST", {
                            "images": frames, "text": text or "What am I looking at?", "system": system}).get("reply")
                except Exception:
                    reply = None  # PC asleep / busy -> paid brain below
            if reply:
                audio = None
                if speak:
                    try:
                        audio = server._speak(reply)
                    except Exception:
                        audio = None
                return {"reply": reply, "images": [], "audio": audio, "local": True}
            content = []
            for f in frames:
                media, data = f[5:].split(";base64,", 1)
                content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
            kind = "a short video" if len(frames) > 1 else "a photo"
            content.append({"type": "text", "text": f"[Live camera from their Jarvis phone app — {len(frames)} "
                            f"frame(s) of {kind}. Describe or answer about what you see. Keep replies "
                            f"conversational; no markdown.] " + (text or "What am I looking at?")})
            return server._answer(uid, content, text or "(live camera)", bool(body.get("speak", True)))

        # --- Daily Awareness (Issue #7): the person's own rolling 24-hour transcript ----------------------
        @app.get("/api/awareness")
        def awareness_today(q: str = "", who=Depends(auth)):
            """Today's timeline (newest first) + the room-listening session. Each person sees only their own."""
            from core import awareness

            uid, _ = who
            with users.acting_as(uid):
                rows = awareness.search(q, limit=400) if q.strip() else awareness.entries()[-400:]
                owner = users.get(uid).get("role") == "owner"
                s = awareness.session_anywhere() if owner else None
            return {"entries": rows[::-1], "session": s, "can_listen": owner, "retention_hours": 24}

        @app.post("/api/awareness/session")
        def awareness_session(body: dict, who=Depends(auth)):
            """Start (needs consent) / pause / resume / stop room listening. Owner only: it's the PC's microphone."""
            from core import awareness

            uid, _ = who
            pcbridge.require_owner(uid)
            action = str(body.get("action", ""))
            if action not in ("start", "pause", "resume", "stop"):
                raise HTTPException(400, "Choose start, pause, resume or stop.")
            try:
                with users.acting_as(uid):
                    return {"session": awareness.control_anywhere(action, bool(body.get("guests")), bool(body.get("consent")))}
            except ValueError as error:
                raise HTTPException(400, str(error)) from None

        @app.get("/api/thread/recent")
        def thread_recent(who=Depends(auth)):
            """This machine's own recent exchanges with the person asking (core.thread): the PC fetches the laptop's
            here, since the laptop's Syncthing sends its changes only on an hourly rescan. Their own rows only."""
            from core import thread
            uid, _ = who
            with users.acting_as(uid):
                return {"uid": uid, "rows": thread.own(uid)}

        @app.post("/api/awareness/delete")
        def awareness_delete(body: dict, who=Depends(auth)):
            """Delete chosen lines, the last N minutes, or everything — on every machine that has a copy."""
            from core import awareness

            uid, _ = who
            ids = body.get("ids") if isinstance(body.get("ids"), list) else None
            minutes = body.get("minutes") if isinstance(body.get("minutes"), (int, float)) and body["minutes"] > 0 else None
            everything = bool(body.get("everything"))
            if not (ids or minutes or everything):
                raise HTTPException(400, "Say what to delete.")
            with users.acting_as(uid):
                if body.get("local_only"):
                    return {**awareness.delete_answer(ids, minutes, everything), "complete": True}
                try:
                    n, complete = awareness.delete_everywhere(ids, minutes, everything)
                except awareness.ThreadNotForgotten as kept:     # a thread ledger kept the words: said, never a 500
                    return {"deleted": kept.deleted, "complete": kept.complete, "thread_kept": True}
            return {"deleted": n, "complete": complete}

        @app.post("/api/awareness/keep")
        def awareness_keep(body: dict, who=Depends(auth)):
            """Save chosen lines long-term in the person's memory vault (nothing else outlives 24 hours)."""
            from core import awareness

            uid, _ = who
            ids = body.get("ids") if isinstance(body.get("ids"), list) else []
            if not ids:
                raise HTTPException(400, "Choose what to keep.")
            with users.acting_as(uid):
                return {"ok": True, "message": awareness.keep(ids)}

        @app.post("/api/eng")
        def engineering(body: dict, who=Depends(auth)):
            """Engineering Eyes: {action: identify|start|check|back|skip|stop|status, image?, question?, goal?}.
            Runs on the GPU PC (here when this is the PC, else relayed); replies can be spoken."""
            from core import engeyes

            uid, _ = who
            pcbridge.require_owner(uid)
            image = body.get("image")
            if isinstance(image, str) and len(image) > MAX_UPLOAD:
                raise HTTPException(413, "That photo is too big.")
            payload = {k: body[k] for k in ("action", "image", "question", "goal") if k in body}
            if pcbridge.local_pc():
                try:
                    with users.acting_as(uid):
                        result = engeyes.handle(payload)
                except engeyes.EngError as error:
                    raise HTTPException(error.status, str(error)) from None
            else:
                with users.acting_as(uid):
                    result = pcbridge.json_request("/api/pc/eng", "POST", payload)  # PCError -> handler
            result["audio"] = None
            if body.get("speak") and result.get("reply"):
                try:
                    result["audio"] = server._speak(result["reply"])
                except Exception as error:
                    diag.swallowed("phone speak", error)
            return result

        @app.post("/api/voice")
        def voice_msg(audio: UploadFile = File(...), speak: str = Form("1"), who=Depends(auth)):
            uid, _ = who
            if server.stt is None:
                raise HTTPException(503, "Speech recognition isn't running")
            from core import gateway, inputwatch, phone as phone_audio, turntrace

            data = audio.file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "That recording is too long.")
            # The same ledger a typed turn has (/api/chat), plus the transcription: until 2026-11-19 a spoken phone
            # turn wrote no timing row at all.
            device = who[1] if len(who) > 1 and isinstance(who[1], dict) else {}
            test = str(device.get("name", "")).startswith("claude-verify")   # a developer's verification device
            started, t0 = time.time(), time.monotonic()
            samples = phone_audio.decode_to_16k(data)
            heard = server.stt.transcribe(samples) if len(samples) > 1600 else ""
            turntrace.span("stt", "whisper", started, time.time())
            if not heard:
                return {"heard": "", "reply": "I didn't catch that. Try again?", "images": [], "audio": None}
            if not test:
                inputwatch.note_turn()      # the owner is here (practice steps aside at once)
            content = [{"type": "text", "text": "[Spoken into their Jarvis phone app.] " + heard}]
            gateway.TEST_TURN.on = test
            try:
                out = server._answer(uid, content, heard, speak == "1", verify=test)
            finally:
                gateway.TEST_TURN.on = False
            took = time.monotonic() - t0
            print(f"[timing] phone turn {took:.1f}s: " + turntrace.record(
                "phone", heard, started, took, took, marks={"total": (started, started + took)}, test=test,
                device=str(device.get("id", ""))))
            return {"heard": heard, **out}

        @app.post("/api/location")
        def set_location(body: dict, who=Depends(auth)):
            """The phone's position (the browser asked him once): reduced to a town and kept as place-phone.json (core/place)."""
            uid, _ = who
            owner_only(uid)
            from core import place
            record = place.update("phone", body.get("lat"), body.get("lon"), body.get("accuracy"))
            if not record:
                raise HTTPException(400, "not a position")
            return {"ok": True, "place": ", ".join(p for p in (record["city"], record["state"]) if p)}

        @app.get("/api/location")
        def get_location(who=Depends(auth)):
            owner_only(who[0])
            from core import place
            return {"place": place.label(), "source": (place.current() or {}).get("source", "")}

        @app.post("/api/push")
        def subscribe(body: dict, who=Depends(auth)):
            uid, device = who
            set_push(uid, device["id"], body.get("subscription"))
            return {"ok": True}

        @app.get("/api/audio/{name}")
        def audio_file(name: str):
            path = CACHE / Path(name).name  # random, unguessable names; no auth so <audio> can play it
            if not path.exists():
                raise HTTPException(404)
            return FileResponse(path, media_type="audio/mpeg")

        @app.get("/api/file/{scope}/{rel:path}")
        def user_file(scope: str, rel: str, who=Depends(auth)):
            uid, _ = who
            if scope not in ("u", "h"):
                raise HTTPException(404)
            base = ((storage.DATA / "users" / uid) if scope == "u" else (storage.DATA / "household")).resolve()
            path = (base / rel).resolve()
            if not path.is_relative_to(base) or not path.is_file():  # real containment, not a name-prefix match
                raise HTTPException(404)
            if scope == "h" and users.get(uid).get("role") != "owner":
                relative = path.relative_to(base)
                parts=tuple(p.casefold() for p in relative.parts)
                if parts[0] == "teaching" or parts[0].startswith('learned-procedures-'):
                    raise HTTPException(403, "Owner-only teaching data")
                if parts[0] == "engineering":
                    from tools.engineering import _index
                    if len(parts)!=3 or parts[1]!='docs':
                        raise HTTPException(403, "Raw library metadata is owner-only")
                    if len(parts) == 3 and parts[1] == "docs":
                        record = next((r for r in _index() if r["id"].casefold() == path.stem.casefold()), None)
                        if not record or record.get("access") == "owner":
                            raise HTTPException(403, "Owner-only teaching source")
            return FileResponse(path)

        @app.get("/api/fitness")
        def fitness_data(who=Depends(auth)):
            """The phone fitness app: plan, today's workout, AFT tests, workouts, Garmin stats (theirs only)."""
            from tools import fitness

            uid, _ = who
            with users.acting_as(uid):
                return fitness.app_data()

        @app.get("/api/kitchen")
        def kitchen_data(who=Depends(auth)):
            """The phone kitchen page: family meal plan, recipes, shopping list, and their own nutrition."""
            from tools import kitchen

            uid, _ = who
            with users.acting_as(uid):
                return kitchen.app_data()

        @app.post("/api/kitchen/scan-receipt")
        def kitchen_scan_receipt(file: UploadFile = File(...), who=Depends(auth)):
            """Phone "eyes": read a receipt photo locally (OCR) and return the grocery items it
            found, for the person to confirm before adding to the pantry. No paid AI, nothing stored."""
            import io

            from core import pcvision
            from tools import pantry

            uid, _ = who
            data = file.file.read(26 * 1024 * 1024 + 1)
            if len(data) > 26 * 1024 * 1024:
                raise HTTPException(413, "That photo is too big (keep it under 25 MB).")
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                lines, _note = pcvision.ocr_image(img)
                text = "\n".join(line["text"] for line in lines)
                with users.acting_as(uid):
                    return {"ok": True, "items": pantry.parse_receipt(text)}
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(400, "Couldn't read that receipt. Try a flatter, well-lit photo.")

        @app.get("/api/kitchen/pantry")
        def kitchen_pantry(who=Depends(auth)):
            from tools import pantry

            uid, _ = who
            with users.acting_as(uid):
                from core import nutri
                return {"items": nutri.pantry_view(pantry.list_items())}

        @app.post("/api/kitchen/pantry")
        def kitchen_pantry_add(body: dict, who=Depends(auth)):
            """Add confirmed items (from a receipt scan or typed) to the household pantry."""
            from tools import pantry

            uid, _ = who
            items = body.get("items") if isinstance(body.get("items"), list) else []
            items = [{"name": str(i.get("name", ""))[:80], "qty": str(i.get("qty", ""))[:30],
                      "source": str(i.get("source", "receipt"))[:16]}
                     for i in items if isinstance(i, dict) and str(i.get("name", "")).strip()][:200]
            if not items:
                raise HTTPException(400, "Send at least one item with a name.")
            with users.acting_as(uid):
                pantry.add_items(items)
                from core import nutri
                return {"ok": True, "items": nutri.pantry_view(pantry.list_items())}

        @app.post("/api/kitchen/pantry/remove")
        def kitchen_pantry_remove(body: dict, who=Depends(auth)):
            from tools import pantry

            uid, _ = who
            name = str(body.get("name", "")).strip()[:80]
            if not name:
                raise HTTPException(400, "Say which item to remove.")
            with users.acting_as(uid):
                from core import nutri
                ok = pantry.remove_item(name)
                return {"ok": ok, "items": nutri.pantry_view(pantry.list_items())}

        @app.post("/api/kitchen/cook")
        def kitchen_cook(body: dict, who=Depends(auth)):
            """Haitian-style recipe from the pantry, using up food first (free local model)."""
            from tools import pantry

            uid, _ = who
            focus = str(body.get("focus", ""))[:200]
            note = str(body.get("note", ""))[:200]
            with users.acting_as(uid):
                return {"ok": True, "recipe": pantry.recipe_from_pantry(focus, note)}

        # -- Kitchen v2, the Training Table (docs/KITCHEN-V2.md): chef + nutritionist, per person -------------------
        def _kitchen_call(uid, fn):
            from core import nutri
            with users.acting_as(uid):
                try:
                    return fn(nutri)
                except nutri.UnknownDish:
                    raise HTTPException(404, "That dish isn't in the kitchen library.")
                except ValueError as error:
                    raise HTTPException(400, str(error))

        # -- Google Calendar sign-in from the app (app/calendar.html): save the key, tap Connect, Allow, paste back --
        def _cal(fn):
            from core import gcal
            try:
                return fn(gcal)
            except gcal.CalendarError as error:
                raise HTTPException(400, str(error))

        def _cal_owner(uid: str) -> bool:
            person = users.all_users().get(uid)             # users.get() falls back to the owner for unknown ids
            return bool(person) and person.get("role") == "owner"

        def _cal_status(g, uid):
            return {**g.link_status(uid), "can_set_key": _cal_owner(uid)}

        @app.get("/api/calendar/link")
        def calendar_link_status(who=Depends(auth)):
            return _cal(lambda g: _cal_status(g, who[0]))

        @app.post("/api/calendar/link/key")
        def calendar_link_key(body: dict, who=Depends(auth)):
            def go(g):
                if not _cal_owner(who[0]):
                    raise HTTPException(403, "Only the owner sets up the family's Google key.")
                g.save_client(str(body.get("json", ""))[:4000], str(body.get("client_id", ""))[:300],
                              str(body.get("client_secret", ""))[:300])
                return _cal_status(g, who[0])
            return _cal(go)

        @app.post("/api/calendar/link/start")
        def calendar_link_start(who=Depends(auth)):
            return _cal(lambda g: {"url": g.start_link(who[0])})

        @app.post("/api/calendar/link/finish")
        def calendar_link_finish(body: dict, who=Depends(auth)):
            return _cal(lambda g: (g.finish_link(who[0], str(body.get("url", ""))[:4000]), _cal_status(g, who[0]))[1])

        @app.post("/api/calendar/link/disconnect")
        def calendar_link_disconnect(who=Depends(auth)):
            def go(g):
                with users.acting_as(who[0]):
                    g.disconnect()
                return _cal_status(g, who[0])
            return _cal(go)

        @app.get("/api/calendar/today")
        def calendar_today(who=Depends(auth)):
            def go(g):
                with users.acting_as(who[0]):
                    start, end = g.day_bounds(g.ownertime.now().date())
                    return {"events": [g.describe(e, with_day=False) for e in g.events(start, end)][:20]}
            return _cal(go)

        @app.get("/api/kitchen/v2")
        def kitchen_v2(who=Depends(auth)):
            """The new Kitchen page: today, the week's plan, dishes with photos and likes, cook-now, pantry, shopping."""
            return _kitchen_call(who[0], lambda n: n.page())

        @app.post("/api/kitchen/taste")
        def kitchen_taste(body: dict, who=Depends(auth)):
            dish_id, vote = str(body.get("dish", ""))[:80], str(body.get("vote", ""))[:10]
            return _kitchen_call(who[0], lambda n: {"taste": n.vote(dish_id, vote)})

        @app.post("/api/kitchen/eat")
        def kitchen_eat(body: dict, who=Depends(auth)):
            dish_id, slot = str(body.get("dish", ""))[:80], str(body.get("slot", ""))[:12]
            try:
                portion = float(body.get("portion") or 1)
            except (TypeError, ValueError):
                portion = 1.0

            def go(n):
                n.eat(dish_id, slot, portion)
                return {"eaten": n.eaten_today()}
            return _kitchen_call(who[0], go)

        @app.post("/api/kitchen/uneat")
        def kitchen_uneat(body: dict, who=Depends(auth)):
            dish_id, slot = str(body.get("dish", ""))[:80], str(body.get("slot", ""))[:12]
            return _kitchen_call(who[0], lambda n: {"eaten": n.uneat(dish_id, slot)})

        @app.post("/api/kitchen/shopping")
        def kitchen_shopping_add(body: dict, who=Depends(auth)):
            """Add items to the household shopping list (every phone sees it)."""
            items = body.get("items") if isinstance(body.get("items"), list) else []
            items = [i for i in items if isinstance(i, dict) and str(i.get("item", "")).strip()]
            if not items:
                raise HTTPException(400, "Send at least one item.")
            return _kitchen_call(who[0], lambda n: {"added": n.add_to_shopping(items)})

        @app.post("/api/kitchen/water")
        def kitchen_water(body: dict, who=Depends(auth)):
            try:
                liters = max(-2.0, min(2.0, float(body.get("liters") or 0.25)))
            except (TypeError, ValueError):
                raise HTTPException(400, "Say how much water, in liters.")
            return _kitchen_call(who[0], lambda n: {"water_l": n.add_water(liters)})

        @app.post("/api/kitchen/weight")
        def kitchen_weight(body: dict, who=Depends(auth)):
            try:
                lbs = float(body.get("lbs"))
            except (TypeError, ValueError):
                raise HTTPException(400, "Send your weight in pounds.")
            return _kitchen_call(who[0], lambda n: {"weighin": n.log_weight(lbs), "targets": n.targets()})

        @app.post("/api/kitchen/swap")
        def kitchen_swap(body: dict, who=Depends(auth)):
            day, slot = str(body.get("date", ""))[:10], str(body.get("slot", ""))[:12]

            def go(n):
                week = n.swap(day, slot)
                return {"week": week, "dishes": n.dishes_for({m for d in week for m in d["meals"].values()})}
            return _kitchen_call(who[0], go)

        @app.post("/api/kitchen/replan")
        def kitchen_replan(body: dict, who=Depends(auth)):
            tight = bool(body.get("budget_tight"))

            def go(n):
                n.week(refresh=True, budget_tight=tight)
                return n.page(budget_tight=tight)
            return _kitchen_call(who[0], go)

        @app.post("/api/kitchen/household")
        def kitchen_household(body: dict, who=Depends(auth)):
            from tools import kitchen
            uid, _ = who
            with users.acting_as(uid):
                data = kitchen._data()
                hh = data.setdefault("household", {})
                if body.get("size") not in (None, ""):
                    try:
                        hh["household_size"] = max(1, min(20, int(body["size"])))
                    except (TypeError, ValueError):
                        raise HTTPException(400, "Household size must be a number.")
                if body.get("budget_week_usd") not in (None, ""):
                    try:
                        hh["weekly_food_budget"] = max(0, min(5000, round(float(body["budget_week_usd"]))))
                    except (TypeError, ValueError):
                        raise HTTPException(400, "Budget must be a number of dollars.")
                storage.save(kitchen.FILE, data)
                return {"household": {"size": hh.get("household_size") or 5,
                                      "budget_week_usd": hh.get("weekly_food_budget"), "toddler": True}}

        @app.post("/api/kitchen/snap")
        def kitchen_snap(file: UploadFile = File(...), kind: str = Form("auto"), who=Depends(auth)):
            """A photo of groceries, a receipt, a plate or a label -> what's in it and what the family can cook now.
            Free vision (this machine's, else the PC's over the relay); nothing is stocked or logged until confirmed."""
            from core import chef
            uid, _ = who
            data = file.file.read(26 * 1024 * 1024 + 1)
            if len(data) > 26 * 1024 * 1024:
                raise HTTPException(413, "That photo is too big (keep it under 25 MB).")
            with users.acting_as(uid):
                from PIL import Image
                try:
                    return chef.snap(data, str(kind)[:12])
                except (OSError, ValueError, Image.DecompressionBombError):
                    raise HTTPException(400, "Couldn't open that photo. Try a JPEG or PNG.")

        @app.get("/api/kitchen/photo/{dish_id}")
        def kitchen_photo(dish_id: str, who=Depends(auth)):
            from core import nutri
            path = nutri.photo_path(dish_id)
            if not path.exists():
                raise HTTPException(404, "No photo yet.")
            return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

        @app.get("/api/kitchen/snap/{snap_id}.jpg")
        def kitchen_snap_image(snap_id: str, who=Depends(auth)):
            import re

            from core import nutri
            if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", snap_id):
                raise HTTPException(404, "No such photo.")
            path = storage.DATA / nutri.SNAP_DIR / f"{snap_id}.jpg"
            if not path.exists():
                raise HTTPException(404, "No such photo.")
            return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

        @app.post("/api/fitness/log")
        def fitness_log(body: dict, who=Depends(auth)):
            """'Done' from the phone app: log today's workout (or a quick custom one)."""
            from tools import fitness

            uid, _ = who
            items = body.get("items") if isinstance(body.get("items"), list) else []
            title = str(body.get("title", "Workout"))[:120]
            from tools import life
            with users.acting_as(uid):
                before = {b["id"] for b in life.achievements(fitness._data())["badges"] if b["earned"]}
                msg = fitness.log_workout(title, items[:40], int(body.get("rpe") or 0))
                data = fitness.app_data()
                new = [b for b in data["achievements"]["badges"] if b["earned"] and b["id"] not in before]
            for b in new:
                notify.notify(f"New badge: {b['name']} {b['icon']}", f"{b['desc']}. Nicely done.", uid, "training")
            return {"ok": True, "message": msg, "data": data, "new_badges": new}

        @app.post("/api/fitness/coach")
        def fitness_coach(body: dict, who=Depends(auth)):
            """One tap on the Trainer page: start, a set, skip, swap, end, a goal or a new plan. The rules in
            core/coach_engine answer on this machine; no model is called."""
            from tools import fitness, trainer

            uid, _ = who
            with users.acting_as(uid):
                message = trainer.page_action(body if isinstance(body, dict) else {})
                return {"ok": True, "message": message, "data": fitness.app_data()}

        @app.get("/api/pca")
        def pca_data(who=Depends(auth)):
            """The Project Captain America page (app/captain.html): onboarding, the 12-week program, today, baselines,
            history and changes. Rules in core/pca_engine answer on this machine; no model is called."""
            from tools import pca

            uid, _ = who
            with users.acting_as(uid):
                return pca.page_data()

        @app.post("/api/pca/action")
        def pca_action(body: dict, who=Depends(auth)):
            """One tap on the Captain America page: an answer, begin, a set, a run, a test, pain, outside load, override."""
            from tools import pca

            uid, _ = who
            with users.acting_as(uid):
                message = pca.page_action(body if isinstance(body, dict) else {})
                if isinstance(message, dict):                # read-only (day, demo videos): no page data to resend
                    return {"ok": True, **message}
                return {"ok": True, "message": message, "data": pca.page_data()}

        # -- the Partner page (app/partner.html): his goals, what Jarvis owes him, the morning report -------------------
        @app.get("/api/partner")
        def partner_data(who=Depends(auth)):
            """The paired person's own goals and commitments (both machines' files merged), the last report said and
            where things stand now. Built in code (core/partner_page); no model is called, nothing is marked reported."""
            from core import partner_page

            uid, _ = who
            with users.acting_as(uid):
                return partner_page.page_data()

        @app.post("/api/partner/goal")
        def partner_goal(body: dict = Body(default=None), who=Depends(auth)):
            """One tap on the Partner page: add / activate / drop / done / undo / next_step. Writes only this machine's
            goals file, for the paired person only (core/commitments writers)."""
            from core import partner_page

            uid, _ = who
            with users.acting_as(uid):
                try:
                    message = partner_page.page_action(body)
                except partner_page.PageError as error:
                    raise HTTPException(error.status, str(error)) from None
                return {"ok": True, "message": message, "data": partner_page.page_data()}

        # -- Jarvis Link (android/jarvis-link): the helper app that plays/pauses/skips Metrolist on his phone --------------
        def link_only(who):
            from tools import pca_link
            if not pca_link.is_link_device(who[1]):
                raise HTTPException(403, "Only the Jarvis Link app can take music commands. Pair it as jarvis-link.")
            return who[0]

        async def link_body(request: Request, limit: int = LINK_BODY_MAX):
            """The phone's JSON report: at most `limit` bytes (refused by Content-Length before reading, and never
            more than the cap + 1 chunk read), nested at most LINK_BODY_DEPTH levels; anything else is a 4xx, never a 500."""
            import json as _json
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > limit:
                raise HTTPException(413, "That report is too big.")
            raw = bytearray()
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > limit:
                    raise HTTPException(413, "That report is too big.")
            if _bracket_depth(bytes(raw)) > LINK_BODY_DEPTH:
                raise HTTPException(400, "That report is nested too deeply.")
            try:
                body = _json.loads(bytes(raw) or b"null")
            except (ValueError, RecursionError, UnicodeDecodeError):
                raise HTTPException(400, "That report is not JSON.") from None
            if _json_depth(body) > LINK_BODY_DEPTH:
                raise HTTPException(400, "That report is nested too deeply.")
            return body

        def _autodj(uid):
            """The music auto-DJ (tools/pca_music.tick) runs whenever the phone talks to this server: on every now-playing
            report and every poll. It runs in its own thread (a pick may search for up to 10 s), at most every 5 s."""
            try:
                from tools import pca_music
                pca_music.tick_soon(uid)
            except Exception as error:  # noqa: BLE001 - never costs the phone its poll
                diag.swallowed("link auto-dj", error, every=600)

        @app.post("/api/link/paircode")
        def link_paircode(who=Depends(auth)):
            """A one-use pairing code (15 minutes) for the Jarvis Link app, for the signed-in person: the same file codes
            'python manage.py pair' makes, so POST /api/pair (the app pairs itself as jarvis-link) accepts it."""
            import secrets as _secrets

            from manage import CODES_FILE, file_codes

            uid, device = who
            from tools import pca_link
            if pca_link.is_link_device(device):
                raise HTTPException(403, "Ask for the code from the Jarvis page on your phone, not from the Link app.")
            with server._paircode_lock:
                codes = file_codes()
                mine = [c for c, v in codes.items() if v.get("uid") == uid and v.get("for") == pca_link.DEVICE]
                for c in mine:                                   # one live Link code per person: a new one replaces it
                    codes.pop(c, None)
                code = f"{_secrets.randbelow(900000) + 100000}"
                while code in codes:
                    code = f"{_secrets.randbelow(900000) + 100000}"
                codes[code] = {"uid": uid, "expires": time.time() + 15 * 60, "for": pca_link.DEVICE}
                storage.save_global(CODES_FILE, codes)
            return {"code": code, "expires_in": 15 * 60}

        @app.get("/api/link/cmd")
        async def link_cmd(wait: float = 0, who=Depends(auth)):
            """The phone's long poll: commands for it, and how long to sleep before the next poll. Async with short
            asyncio sleeps, so waiting phones never hold a worker thread (the queue is a small file read every 0.5 s,
            which also sees commands the PC queued once Syncthing brings them over)."""
            from tools import pca_link

            uid = link_only(who)
            _autodj(uid)
            try:
                wait = min(max(float(wait), 0.0), float(pca_link.MAX_WAIT))
            except (TypeError, ValueError):
                wait = 0.0
            if wait != wait:                                     # nan
                wait = 0.0
            deadline = time.monotonic() + wait
            while True:
                with users.acting_as(uid):                       # no await inside: the person is set for this read only
                    out = pca_link.take()
                if out["commands"] or out["sleep"] > 0 or time.monotonic() >= deadline:
                    try:                                         # Health Connect: ask the phone for his watch data now
                        from tools import pca_health
                        with users.acting_as(uid):
                            out["health"] = pca_health.sync_wanted()
                    except Exception as error:  # noqa: BLE001 - never costs the phone its poll
                        diag.swallowed("link health flag", error, every=600)
                    return out
                await asyncio.sleep(0.5)

        @app.post("/api/link/state")
        async def link_state(request: Request, who=Depends(auth)):
            from tools import pca_link

            uid = link_only(who)
            body = await link_body(request)
            with users.acting_as(uid):
                try:
                    out = pca_link.report_state(body)
                except pca_link.LinkError as e:
                    raise HTTPException(400, str(e)) from None
            _autodj(uid)                                         # a track change: the auto-DJ looks at it (in the background)
            return out

        @app.post("/api/link/result")
        async def link_result(request: Request, who=Depends(auth)):
            from tools import pca_link

            uid = link_only(who)
            body = await link_body(request)
            with users.acting_as(uid):
                try:
                    return pca_link.report_result(body)
                except pca_link.LinkError as e:
                    raise HTTPException(400, str(e)) from None

        @app.post("/api/link/health")
        async def link_health(request: Request, who=Depends(auth)):
            """Garmin data the Jarvis Link app read from Android Health Connect (tools/pca_health): one chunk per POST,
            link device only, at most HEALTH_BODY_MAX bytes; every field is validated there and the unknown ones dropped."""
            from tools import pca_health

            uid = link_only(who)
            body = await link_body(request, HEALTH_BODY_MAX)
            with users.acting_as(uid):
                try:
                    return pca_health.ingest(body)
                except pca_health.HealthError as e:
                    raise HTTPException(400, str(e)) from None
                except Exception as error:  # noqa: BLE001 - a validation bug is a refused report, never a 500
                    diag.swallowed("link health ingest", error)
                    raise HTTPException(400, "Jarvis could not read that health report; nothing from it was stored.") from None

        @app.get("/api/health/status")
        def health_status(who=Depends(auth)):
            """Any of the person's devices: is his Garmin connected through Health Connect, the last sync, the counts,
            the newest night and run (only what the phone sent)."""
            from tools import pca_health

            uid, _ = who
            with users.acting_as(uid):
                return pca_health.page_status()

        @app.get("/api/link/status")
        def link_status(who=Depends(auth)):
            """Any of the person's devices may read this (the page shows it); only the link device takes commands."""
            from tools import pca_link

            uid, _ = who
            with users.acting_as(uid):
                return pca_link.status(uid)

        @app.get("/api/link/apk")
        def link_apk(who=Depends(auth)):
            from tools import pca_link

            path = pca_link.apk_path()
            if path is None:
                raise HTTPException(404, "The Jarvis Link app has not been built on this machine yet.")
            return FileResponse(path, media_type="application/vnd.android.package-archive", filename="JarvisLink.apk",
                                headers={"Cache-Control": "no-store"})

        @app.post("/api/fitness/checkin")
        def fitness_checkin(body: dict, who=Depends(auth)):
            """The morning check-in card: sleep, energy, soreness (+ Garmin readiness / resting HR if they have them)."""
            from tools import fitness

            def num(k, cast):
                try:
                    return cast(body[k]) if body.get(k) not in (None, "") else None
                except (TypeError, ValueError):
                    return None
            uid, _ = who
            with users.acting_as(uid):
                msg = fitness.log_checkin(num("sleep_hours", float) or 0, num("energy", int) or 0, num("soreness", int) or 0,
                                          num("readiness", int), num("resting_hr", int))
                return {"ok": True, "message": msg, "data": fitness.app_data()}

        # -- memory vault ("Jarvis, remember this"): recordings, videos, photos, notes ------------
        VAULT_MAX = 2 * 1024 ** 3  # a long video from the phone

        # -- the language teacher (app/languages.html): pick a language, run a lesson, see progress -------------
        def language_call(who, fn, *args, session="", **kwargs):
            """Run one language action as the paired person, in the page's own session (a lesson on the Languages
            page never captures what he says to Jarvis by voice or chat); what he can fix is a 400 with the sentence."""
            from tools import langlesson

            uid, _ = who
            try:
                with users.acting_as(uid), langlesson.on_page(str(session)):
                    return fn(*args, **kwargs)
            except langlesson.LessonError as error:
                raise HTTPException(400, str(error)) from None

        @app.get("/api/languages")
        def languages_data(session: str = "", who=Depends(auth)):
            from tools import langlesson

            return language_call(who, langlesson.overview, session=session)

        @app.post("/api/languages/lesson/start")
        def languages_start(body: dict, who=Depends(auth)):
            from tools import langlesson

            try:
                minutes = int(body.get("minutes") or 10)
            except (TypeError, ValueError):
                raise HTTPException(400, "The length must be a number of minutes.") from None
            sid = langlesson.new_page_session()
            out = language_call(who, langlesson.lesson_start, str(body.get("language") or "")[:40], minutes,
                                bool(body.get("from_zero")), session=sid)
            return {**out, "session": sid}

        @app.post("/api/languages/lesson/{action}")
        def languages_step(action: str, body: dict, who=Depends(auth)):
            from tools import langlesson

            sid = str(body.get("session") or "")
            if action == "answer":
                return language_call(who, langlesson.lesson_answer, str(body.get("answer") or "")[:400], session=sid)
            if action == "repeat":
                return language_call(who, langlesson.lesson_repeat, bool(body.get("slower")), session=sid)
            if action == "skip":
                return language_call(who, langlesson.lesson_skip, session=sid)
            if action == "stop":
                return language_call(who, langlesson.lesson_stop, session=sid)
            raise HTTPException(404, "No such lesson action.")

        @app.post("/api/languages/phrase")
        def languages_phrase(body: dict, who=Depends(auth)):
            """"How do you say X" (to="language") or "what does X mean" (to="english") from the page."""
            from tools import langlesson

            fn = langlesson.meaning if body.get("to") == "english" else langlesson.say
            return language_call(who, fn, str(body.get("phrase") or "")[:120], str(body.get("language") or "")[:40],
                                 session=str(body.get("session") or ""))

        # -- his Creole voice recording (app/voice-record.html): the script, his takes, his corrections -------
        # The voice Jarvis speaks with is trained on the owner's reading, so only the owner records; everything is
        # read and written in the folder of the person the device is paired as, never one named in the request.
        def record_call(who, fn, *args, **kwargs):
            from core import kreyol_record

            uid, _ = who
            if users.get(uid).get("role") != "owner":
                raise HTTPException(403, "Only the owner records the voice.")
            try:
                return fn(*args, uid=uid, **kwargs)
            except kreyol_record.RecordError as error:
                raise HTTPException(400, str(error)) from None

        @app.get("/api/voice-record")
        def voice_record_data(who=Depends(auth)):
            from core import kreyol_record

            record_call(who, lambda uid: None)
            try:        # the PC decodes what the laptop could not: one take a load, and never at the page's expense
                record_call(who, lambda uid: kreyol_record.convert_pending(uid, limit=1))
            except Exception as error:
                print(f"[voice-record] a waiting take was not converted ({str(error)[:80]})")
            return record_call(who, kreyol_record.overview)

        @app.post("/api/voice-record/take")
        async def voice_record_take(request: Request, who=Depends(auth)):
            from starlette.concurrency import run_in_threadpool

            from core import kreyol_record

            record_call(who, lambda uid: None)
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > kreyol_record.MAX_BYTES + 65536:      # refused by its declared size, before the body is read
                raise HTTPException(413, "That recording is too large. One sentence a take, please.")
            form = await request.form()
            audio = form.get("audio")
            if audio is None or not hasattr(audio, "read"):
                raise HTTPException(400, "No recording came with that. Try again.")
            data = await audio.read(kreyol_record.MAX_BYTES + 1)
            take = await run_in_threadpool(record_call, who, lambda uid: kreyol_record.add_take(
                str(form.get("sentence") or ""), data, audio.filename or "", audio.content_type or "",
                str(form.get("seconds") or "0"), uid=uid))
            return {"ok": True, "take": take, "data": await run_in_threadpool(record_call, who, kreyol_record.overview)}

        @app.delete("/api/voice-record/take/{take_id}")
        def voice_record_take_remove(take_id: str, who=Depends(auth)):
            from core import kreyol_record

            if not record_call(who, lambda uid: kreyol_record.delete_take(take_id, uid)):
                raise HTTPException(404, "No such take.")
            return {"ok": True, "data": record_call(who, kreyol_record.overview)}

        @app.get("/api/voice-record/take/{take_id}/audio")
        def voice_record_take_audio(take_id: str, who=Depends(auth)):
            from core import kreyol_record

            path = record_call(who, lambda uid: kreyol_record.audio_path(take_id, uid))
            if path is None:
                raise HTTPException(404, "No such take.")
            return FileResponse(path)

        @app.post("/api/voice-record/sentence")
        def voice_record_sentence(body: dict, who=Depends(auth)):
            from core import kreyol_record

            saved = record_call(who, kreyol_record.save_sentence, str(body.get("id") or ""), str(body.get("text") or ""))
            return {"ok": True, "sentence": saved, "data": record_call(who, kreyol_record.overview)}

        # -- the study companion (app/study.html): his course material, quizzes, flashcards, progress ----------
        def study_call(who, fn, *args, session="", **kwargs):
            """Run one study action as the paired person, in the page's own session (a quiz on the Study page never
            captures what he says to Jarvis by voice or chat); what he can fix (no such chapter, unreadable file) is
            a 400 with the sentence to show him."""
            from core import study_store
            from tools import study

            uid, _ = who
            try:
                with users.acting_as(uid), study.on_page(str(session)):
                    return fn(*args, **kwargs)
            except study_store.StudyError as error:
                raise HTTPException(400, str(error)) from None

        def study_number(value, default):
            try:
                return int(value or default)
            except (TypeError, ValueError):
                raise HTTPException(400, "That must be a number.") from None

        @app.get("/api/study")
        def study_data(who=Depends(auth)):
            from tools import study

            return study_call(who, study.overview)

        @app.post("/api/study/material")
        def study_material(course: str = Form(...), title: str = Form(""), text: str = Form(""),
                           file: UploadFile | None = File(None), who=Depends(auth)):
            """Add notes (text) or a file (PDF, text, markdown) to a course; the course is created when new."""
            from core import study_store

            if file is not None and file.filename:
                data = file.file.read(study_store.MAX_BYTES + 1)
                name = Path(file.filename).name
                info = study_call(who, study_store.add_material, course, title or Path(name).stem, data, name)
            elif text.strip():
                if len(text) > 2 * 1024 * 1024:
                    raise HTTPException(400, "Those notes are too long to add in one piece. Split them by chapter.")
                info = study_call(who, study_store.add_material, course, title or "Notes", text, "notes.md")
            else:
                raise HTTPException(400, "Choose a file or paste your notes.")
            from tools import study

            return {"ok": True, "added": info, "data": study_call(who, study.overview)}

        @app.delete("/api/study/material/{material_id}")
        def study_material_remove(material_id: str, who=Depends(auth)):
            from core import study_store
            from tools import study

            if not study_call(who, study_store.remove_material, material_id):
                raise HTTPException(404, "No such material.")
            return {"ok": True, "data": study_call(who, study.overview)}

        @app.post("/api/study/quiz")
        def study_quiz_start(body: dict, who=Depends(auth)):
            from tools import study

            sid = study.new_page_session()
            out = study_call(who, study.quiz_start, str(body.get("course") or ""), str(body.get("chapter") or "")[:200],
                             study_number(body.get("count"), 5), str(body.get("kind") or "mixed"), session=sid)
            return dict(out, session=sid)

        @app.post("/api/study/answer")
        def study_quiz_answer(body: dict, who=Depends(auth)):
            from tools import study

            return study_call(who, study.quiz_answer, str(body.get("answer") or "")[:2000], str(body.get("id") or ""),
                              session=body.get("session") or "")

        @app.post("/api/study/stop")
        def study_quiz_stop(body: dict, who=Depends(auth)):
            from tools import study

            return study_call(who, study.stop, session=body.get("session") or "")

        @app.post("/api/study/explain")
        def study_explain(body: dict, who=Depends(auth)):
            from tools import study

            return study_call(who, study.explain, str(body.get("topic") or "")[:200], str(body.get("level") or "")[:40],
                              str(body.get("course") or ""), bool(body.get("another")), session=body.get("session") or "page")

        @app.get("/api/study/concepts")
        def study_concepts(course: str = "", who=Depends(auth)):
            """What the Teach view lists: the course's concepts by chapter, each with the rung he is on."""
            from tools import study

            return study_call(who, study.concepts, course[:200])

        @app.get("/api/study/board")
        def study_board_last(who=Depends(auth)):
            """The explanation he last got by voice or chat, with its whiteboard scene (null when there is none)."""
            from tools import study

            uid, _ = who
            with users.acting_as(uid):
                return {"board": study.last_board()}

        @app.get("/api/study/cards")
        def study_cards(course: str = "", who=Depends(auth)):
            from tools import study

            return {"due": study_call(who, study.cards_due, course)}

        @app.post("/api/study/cards")
        def study_cards_make(body: dict, who=Depends(auth)):
            from tools import study

            out = study_call(who, study.cards_make, str(body.get("course") or ""), str(body.get("chapter") or "")[:200],
                             study_number(body.get("count"), 8))
            return dict(out, due=study_call(who, study.cards_due, out["course"]))

        @app.post("/api/study/cards/review")
        def study_cards_review(body: dict, who=Depends(auth)):
            from tools import study

            return {"ok": True, "card": study_call(who, study.card_review, str(body.get("id") or ""), str(body.get("rating") or ""))}

        @app.post("/api/study/plan")
        def study_plan(body: dict, who=Depends(auth)):
            """The plan up to an exam date; a date given here is saved on his own course."""
            from core import study_engine, study_store
            from datetime import date as _date

            def run():
                course = study_store.find_course(str(body.get("course") or ""))
                if not course:
                    raise study_store.StudyError("No such course.")
                when = str(body.get("exam_date") or "").strip()
                if when:
                    try:
                        _date.fromisoformat(when)
                    except ValueError:
                        raise study_store.StudyError("The exam date must be a calendar date.") from None
                    if not course.get("builtin"):
                        study_store.set_exam_date(course["id"], when)
                return study_engine.plan(course["id"], when or course.get("exam_date"))
            return study_call(who, run)

        @app.post("/api/memory/upload")
        def memory_upload(file: UploadFile = File(...), kind: str = Form("audio"), note: str = Form(""),
                          who=Depends(auth)):
            import tempfile

            from core import vault

            uid, _ = who
            if kind not in ("audio", "video", "photo"):
                raise HTTPException(400, "kind must be audio, video or photo")
            ext = Path(file.filename or "").suffix.lower()[:6] or {"audio": ".webm", "video": ".webm", "photo": ".jpg"}[kind]
            total = 0
            with tempfile.NamedTemporaryFile(delete=False, dir=vault.root() if vault.root().exists() else None,
                                             suffix=ext) as tmp:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > VAULT_MAX:
                        tmp.close()
                        Path(tmp.name).unlink(missing_ok=True)
                        raise HTTPException(413, "That recording is over 2 GB.")
                    tmp.write(chunk)
            item = vault.add(uid, kind, src=Path(tmp.name), ext=ext, mime=file.content_type or "", note=note)
            return {"ok": True, "item": item}

        @app.post("/api/memory/note")
        def memory_note(body: dict, who=Depends(auth)):
            from core import vault

            uid, _ = who
            text = str(body.get("text", "")).strip()[:20000]
            if not text:
                raise HTTPException(400, "Empty note")
            title = str(body.get("title", ""))[:200]
            return {"ok": True, "item": vault.add(uid, "note", text=text, title=title, note=title,
                                                  source=str(body.get("source", "phone"))[:20])}

        @app.get("/api/memory/search")
        def memory_search(q: str = "", start: str = "", end: str = "", kind: str = "", limit: int = 40,
                          who=Depends(auth)):
            from core import vault

            uid, _ = who
            return {"items": vault.search(uid, q, start, end, kind, limit)}

        @app.get("/api/memory/stats")
        def memory_stats(who=Depends(auth)):
            from core import vault

            return vault.stats()

        @app.get("/api/memory/item/{item_id}")
        def memory_item(item_id: str, who=Depends(auth)):
            from core import vault

            uid, _ = who
            return {"item": vault.get(item_id, uid)}

        @app.delete("/api/memory/item/{item_id}")
        def memory_delete(item_id: str, who=Depends(auth)):
            from core import vault

            uid, _ = who
            return {"ok": vault.delete(item_id, uid)}

        @app.get("/api/memory/file/{item_id}")
        def memory_file(item_id: str, who=Depends(auth)):
            from core import vault

            uid, _ = who
            item = vault.get(item_id, uid)
            path = vault.file_path(item) if item else None
            if not path:
                raise HTTPException(404)
            return FileResponse(path, media_type=item.get("mime") or None)

        # -- Alexa skill bridge: your Echo talks to Jarvis (enabled once ALEXA_SKILL_ID is set) --
        def _verify_alexa(raw: bytes, headers) -> bool:
            """Confirm the request really came from Amazon Alexa (cert chain + signature + timestamp)."""
            import base64
            import urllib.request
            from datetime import datetime, timezone
            from urllib.parse import urlparse

            from cryptography import x509
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            chain = headers.get("signaturecertchainurl")
            sig = headers.get("signature-256") or headers.get("signature")
            if not chain or not sig:
                return False
            u = urlparse(chain)
            if u.scheme != "https" or u.hostname != "s3.amazonaws.com" or (u.port or 443) != 443 \
                    or not os.path.normpath(u.path).replace("\\", "/").startswith("/echo.api/"):
                return False
            try:
                pem = urllib.request.urlopen(chain, timeout=10).read()
                certs = x509.load_pem_x509_certificates(pem)
                leaf = certs[0]
                now = datetime.now(timezone.utc)
                if not (leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc):
                    return False
                sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
                if "echo-api.amazon.com" not in sans:
                    return False
                leaf.public_key().verify(base64.b64decode(sig), raw, padding.PKCS1v15(), hashes.SHA256())
                return True
            except Exception:
                return False

        @app.post("/api/alexa")
        async def alexa(request: Request):
            skill_id = os.getenv("ALEXA_SKILL_ID", "")
            if not skill_id:
                raise HTTPException(503, "Alexa isn't set up yet.")
            raw = await request.body()
            if os.getenv("ALEXA_VERIFY", "on").lower() != "off" and not _verify_alexa(raw, request.headers):
                raise HTTPException(403, "Request signature invalid.")
            body = json.loads(raw)
            ts = (body.get("request") or {}).get("timestamp", "")
            try:
                from datetime import datetime, timezone

                age = abs((datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds())
                if age > 150:
                    raise HTTPException(403, "Request too old.")
            except (ValueError, TypeError):
                pass
            app_id = (((body.get("session") or {}).get("application") or {}).get("applicationId")
                      or ((body.get("context") or {}).get("System") or {}).get("application", {}).get("applicationId"))
            if app_id != skill_id:
                raise HTTPException(403, "Not this skill.")
            uid = os.getenv("ALEXA_USER", "owner")
            req = body.get("request") or {}
            rtype = req.get("type")

            def speak(text, end=False):
                return {"version": "1.0", "response": {"outputSpeech": {"type": "PlainText", "text": text[:7000]},
                        "shouldEndSession": end}}
            if rtype == "LaunchRequest":
                return speak("Jarvis here. What can I do for you?")
            if rtype == "SessionEndedRequest":
                return speak("", True)
            intent = (req.get("intent") or {})
            name = intent.get("name", "")
            if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
                return speak("Right away.", True)
            if name == "AMAZON.HelpIntent":
                return speak("Ask me anything — the homelab, your schedule, a reminder, dinner ideas, or to control your PC.")
            if name == "AMAZON.NavigateHomeIntent":
                return speak("Sure.", True)
            if name == "AMAZON.FallbackIntent":
                return speak("I didn't quite catch that. Try starting with 'tell me' or 'ask' — like, tell me the home lab status.")
            slots = intent.get("slots") or {}
            utterance = (slots.get("query") or {}).get("value") or (slots.get("text") or {}).get("value") or ""
            if not utterance:
                return speak("I didn't catch that. What would you like?")
            out = server._answer(uid, [{"type": "text", "text": "[Spoken to Jarvis through an Alexa Echo. Reply in one "
                                        "or two short spoken sentences, no markdown.] " + utterance}], utterance, speak=False)
            return speak(out.get("reply") or "I'm not sure how to help with that.")

        # Shared authenticated PC routes: explicit role dispatch and resumable jobs.
        from core import pcroutes
        pcroutes.install(app, auth)

        # -- the 3D dressing room (app/wardrobe.html): closet, looks, stylist, discover --------------------
        _AV_LABEL = {"owner": "Alex", "maya": "Maya"}
        _dress_jobs = {"t": 0, "jobs": {}, "down": 0}

        def _dress_pc(path, body, uid):
            """Relay to the PC as the owner link, on behalf of the signed-in person."""
            with users.acting_as("owner"):
                return pcbridge.json_request(path, "POST", {**body, "person": uid})

        @app.get("/api/dress")
        def dress_data(who=Depends(auth)):
            from tools import dressing
            uid, _ = who
            with users.acting_as(uid):
                if pcbridge.local_pc():
                    dressing.import_pending()
                    return dressing.data()
                jobs = {}
                waiting = any(i["job"] and i["job"]["state"] == "waiting" for i in dressing.closet())
                if waiting and time.time() - _dress_jobs["down"] > 60:
                    try:
                        jobs = _dress_pc("/api/pc/dress/closet", {"op": "jobs"}, uid).get("jobs", {})
                    except pcbridge.PCError:
                        _dress_jobs["down"] = time.time()
                return dressing.data(jobs)

        @app.post("/api/dress/avatar")
        def dress_avatar(body: dict, who=Depends(auth)):
            from tools import dressing
            if len(json.dumps(body)) > 4000:
                raise HTTPException(413, "Too large.")
            with users.acting_as(who[0]):
                return {"avatar": dressing.set_avatar_cfg(body)}

        @app.post("/api/dress/closet")
        def dress_closet(body: dict, who=Depends(auth)):
            """Add (photo), correct, re-photo, reprocess or delete a closet item. The PC does the work."""
            from tools import dressing
            uid, _ = who
            op, args = str(body.get("op", "")), body.get("args") or {}
            if op not in ("add", "update", "photo", "reprocess", "delete") or not isinstance(args, dict):
                raise HTTPException(400, "Unknown closet change.")
            if len(json.dumps(args)) > 28 * 1024 * 1024:
                raise HTTPException(413, "That photo is too big.")
            try:
                with users.acting_as(uid):
                    if pcbridge.local_pc():
                        return dressing.closet_op(op, args)
                try:
                    return _dress_pc("/api/pc/dress/closet", {"op": op, "args": args}, uid)
                except pcbridge.PCError as error:
                    if op == "add" and error.status in (502, 503, 504):
                        with users.acting_as(uid):
                            return dressing.queue_add(args)
                    raise HTTPException(error.status, str(error)) from None
            except ValueError as error:
                raise HTTPException(400, str(error)) from None

        @app.post("/api/dress/current")
        def dress_current(body: dict, who=Depends(auth)):
            from tools import dressing
            items = body.get("items")
            if not isinstance(items, dict) or len(json.dumps(body)) > 8000:
                raise HTTPException(400, "Send the worn items.")
            with users.acting_as(who[0]):
                return dressing.set_current(items, str(body.get("name", "")), str(body.get("why", "")))

        @app.post("/api/dress/looks")
        def dress_looks(body: dict, who=Depends(auth)):
            from tools import dressing
            op = body.get("op")
            if len(json.dumps(body)) > 1_300_000:
                raise HTTPException(413, "Too large.")
            with users.acting_as(who[0]):
                if op == "save":
                    if not isinstance(body.get("items"), dict) or not body["items"]:
                        raise HTTPException(400, "Put something on the avatar first.")
                    row = dressing.save_look(str(body.get("name", "")), body["items"], str(body.get("occasion", ""))[:40],
                                             str(body.get("thumb", "")), bool(body.get("favorite")), str(body.get("id", ""))[:12])
                elif op == "update":
                    row = dressing.update_look(str(body.get("id", "")), **{k: body[k] for k in ("name", "occasion", "favorite") if k in body})
                elif op == "delete":
                    row = dressing.delete_look(str(body.get("id", "")))
                else:
                    raise HTTPException(400, "Unknown look change.")
                return {"look": row, "looks": dressing.looks()}

        @app.post("/api/dress/style")
        def dress_style(body: dict, who=Depends(auth)):
            """The stylist: outfits from the person's own closet for a request; the best one goes on the avatar."""
            from tools import dressing
            req = str(body.get("request", "")).strip()[:300]
            if not req:
                raise HTTPException(400, "Tell me what you're dressing for.")
            with users.acting_as(who[0]):
                cur = dressing.current()
                base = body.get("base") if isinstance(body.get("base"), dict) else {"items": cur.get("items", {}), "_target": cur.get("_target")}
                parsed = dressing.parse_request(req)
                looks = dressing.suggest(req, base=base, n=max(3, parsed["n"]), include_discover=bool(body.get("discover")),
                                         avoid=[base.get("items")] if parsed["better"] and base.get("items") else None)
                if not looks:
                    return {"looks": [], "message": "Your closet needs a few more pieces first — add clothes with a photo."}
                row = dressing.set_current(looks[0]["items"], looks[0]["name"], looks[0]["why"], by="stylist", target=looks[0]["_target"])
                return {"looks": looks, "current": row}

        @app.post("/api/dress/discover")
        def dress_discover(body: dict, who=Depends(auth)):
            from tools import dressing
            uid, _ = who
            payload = {"op": "status"} if body.get("op") == "status" else {"query": str(body.get("query", "")).strip()[:160]}
            if "query" in payload and not payload["query"]:
                raise HTTPException(400, "Describe what to look for.")
            if pcbridge.local_pc():
                if "query" in payload:
                    dressing.discover_async(payload["query"], uid)
                return dressing.discover_status()
            try:
                return _dress_pc("/api/pc/dress/discover", payload, uid)
            except pcbridge.PCError as error:
                raise HTTPException(error.status, "Online search runs on your PC: " + str(error)) from None

        # -- the Wardrobe page (app/wardrobe.html): the person's own photo, their closet, photoreal try-ons, looks, plan --
        def _closet_people(uid):
            """Who this phone may dress: the owner manages both closets; anyone else has their own."""
            from tools import fitting
            mine = users.get(uid).get("role") == "owner"
            ids = [p for p in fitting.PEOPLE if p in users.all_users()] if mine else []
            if uid not in ids:
                ids.insert(0, uid)
            return [{"id": p, "name": str(users.all_users().get(p, {}).get("name") or _AV_LABEL.get(p) or p.title()), "me": p == uid}
                    for p in ids]

        def _closet_target(uid, person):
            person = str(person or "").strip()
            if not person or person == uid:
                return uid
            if users.get(uid).get("role") != "owner" or person not in users.all_users():
                raise HTTPException(403, "You can only open your own closet.")
            return person

        def _closet_state(uid, person):
            from tools import fitting
            with users.acting_as(person):
                return {**fitting.state(), "people": _closet_people(uid)}

        @app.get("/api/closet")
        def closet_state(person: str = "", who=Depends(auth)):
            uid, _ = who
            return _closet_state(uid, _closet_target(uid, person))

        @app.get("/api/closet/file/{person}/{rel:path}")
        def closet_file(person: str, rel: str, who=Depends(auth)):
            """A picture from a person's fashion folder (their photo, garments, cut-outs, try-ons). Images only."""
            from tools import fashion
            uid, _ = who
            with users.acting_as(_closet_target(uid, person)):
                base = fashion.fdir().resolve()
            path = (base / rel).resolve()
            if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower() not in fashion.IMAGE_EXT:
                raise HTTPException(404)
            return FileResponse(path, headers={"Cache-Control": "private, max-age=600"})

        @app.post("/api/closet/photo")
        def closet_photo(person: str = "", file: UploadFile = File(...), who=Depends(auth)):
            """Set or replace the person's full-body photo: the picture every try-on dresses."""
            from tools import fashion
            uid, _ = who
            target = _closet_target(uid, person)
            data = file.file.read(26 * 1024 * 1024 + 1)
            if len(data) > 26 * 1024 * 1024:
                raise HTTPException(413, "That photo is too big (keep it under 25 MB).")
            try:
                with users.acting_as(target):
                    fashion.save_avatar_photo(data)
            except Exception:
                raise HTTPException(400, "That file isn't a usable photo. Try a clear head-to-toe picture.") from None
            return _closet_state(uid, target)

        @app.post("/api/closet/garment")
        def closet_garment(body: dict, who=Depends(auth)):
            """Add (photo from the camera or gallery), edit the tags of, re-photo or delete a garment in a person's closet.
            The PC owns wardrobe.json and digitises the photo; the laptop relays, and keeps an add until the PC is back."""
            from tools import dressing
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            op, args = str(body.get("op", "")), body.get("args") or {}
            if op not in ("add", "update", "photo", "reprocess", "delete") or not isinstance(args, dict):
                raise HTTPException(400, "Unknown closet change.")
            if len(json.dumps(args)) > 28 * 1024 * 1024:
                raise HTTPException(413, "That photo is too big.")
            if op in ("add", "photo") and not args.get("photo"):
                raise HTTPException(400, "Take or choose a photo of the garment.")
            try:
                if pcbridge.local_pc():
                    with users.acting_as(target):
                        out = dressing.closet_op(op, args)
                else:
                    try:
                        out = _dress_pc("/api/pc/dress/closet", {"op": op, "args": args}, target)
                    except pcbridge.PCError as error:
                        if op == "add" and error.status in (502, 503, 504):
                            with users.acting_as(target):
                                out = dressing.queue_add(args)
                        else:
                            raise HTTPException(error.status, "Your PC keeps the closet and it isn't reachable right now. "
                                                              "Wake it and try again.") from None
            except (ValueError, KeyError) as error:
                raise HTTPException(400, str(error) or "That change didn't work.") from None
            return {**out, "state": _closet_state(uid, target)}

        @app.post("/api/closet/tryon")
        def closet_tryon(body: dict, who=Depends(auth)):
            """Queue a photoreal try-on of the person in their own garments. Returns at once; the page polls /api/closet/jobs."""
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            ids = body.get("items")
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
                    or any(not isinstance(g, str) or not g or len(g) > 100 for g in ids)):
                raise HTTPException(400, "Pick the pieces to try on.")
            try:
                with users.acting_as(target):
                    job = fitting.enqueue(ids, str(body.get("notes", ""))[:200], by=uid)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            return {"job": job, "gpu": fitting.gpu()}

        @app.post("/api/closet/prepare")
        def closet_prepare(body: dict, who=Depends(auth)):
            """"Prepare my closet": make the clean cut-outs of every piece that has a photo, in the background."""
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            try:
                with users.acting_as(target):
                    return {"prepare": fitting.prepare_closet(target)}
            except ValueError as error:
                raise HTTPException(400, str(error)) from None

        @app.get("/api/closet/prepare")
        def closet_prepare_status(person: str = "", who=Depends(auth)):
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, person)
            return {"prepare": fitting.prepare_status(target)}

        @app.get("/api/closet/jobs")
        def closet_jobs(person: str = "", who=Depends(auth)):
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, person)
            with users.acting_as(target):
                return {"jobs": fitting.jobs(target), "gpu": fitting.gpu(), "fits": fitting.fits()}

        @app.post("/api/closet/jobs/cancel")
        def closet_jobs_cancel(body: dict, who=Depends(auth)):
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            return {"ok": fitting.cancel(str(body.get("id", "")), target), "jobs": fitting.jobs(target)}

        @app.post("/api/closet/fit/delete")
        def closet_fit_delete(body: dict, who=Depends(auth)):
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            with users.acting_as(target):
                fitting.delete_fit(str(body.get("id", "")))
            return _closet_state(uid, target)

        @app.post("/api/closet/look")
        def closet_look(body: dict, who=Depends(auth)):
            """Save the composed outfit as a look, rename or favourite one, or delete it."""
            from tools import dressing, fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            op = str(body.get("op", ""))
            try:
                with users.acting_as(target):
                    if op == "save":
                        ids = body.get("items")
                        if not isinstance(ids, list) or not ids or len(ids) > 8:
                            raise ValueError("Pick at least one piece from the closet first.")
                        fitting.save_look(str(body.get("name", "")), ids, str(body.get("occasion", "")),
                                          favorite=bool(body.get("favorite")))
                    elif op == "update":
                        if not dressing.update_look(str(body.get("id", ""))[:12],
                                                    **{k: (bool(body[k]) if k == "favorite" else str(body[k])[:60])
                                                       for k in ("name", "occasion", "favorite") if k in body}):
                            raise ValueError("That look isn't saved any more.")
                    elif op == "delete":
                        dressing.delete_look(str(body.get("id", ""))[:12])
                    else:
                        raise ValueError("Unknown look change.")
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            return _closet_state(uid, target)

        @app.post("/api/closet/style")
        def closet_style(body: dict, who=Depends(auth)):
            """What should I wear: outfits scored from the person's own closet for an occasion and the weather."""
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            with users.acting_as(target):
                return fitting.stylist(str(body.get("occasion", ""))[:200], str(body.get("weather", ""))[:40])

        @app.post("/api/closet/plan")
        def closet_plan(body: dict, who=Depends(auth)):
            from tools import fitting
            uid, _ = who
            target = _closet_target(uid, body.get("person"))
            try:
                with users.acting_as(target):
                    return {"week": fitting.plan(str(body.get("date", "")), str(body.get("look_id", ""))[:12])}
            except ValueError as error:
                raise HTTPException(400, str(error)) from None

        # -- the PC companion and Command Center: timers, focus, countdowns, weekly report, lists, media --
        def owner_only(uid: str) -> None:
            if users.get(uid).get("role") != "owner":
                raise HTTPException(403, "Owner only.")

        def camera_viewer(uid: str) -> None:
            """The house cameras: the owner and his spouse have the same full access (owner, 2026-11-20: "my wife is an
            owner so she should have access to everything too")."""
            from core.homecam import CAMERA_ROLES
            if users.get(uid).get("role") not in CAMERA_ROLES:
                raise HTTPException(403, "Owner only.")

        @app.get("/api/notifications")
        def notifications_get(who=Depends(auth)):
            uid, device = who
            return {"items": notify.history(uid), "prefs": notify.prefs(uid), "push": bool(device.get("push")),
                    "categories": [{"id": k, "label": v[0], "desc": v[1], "urgent": v[2]} for k, v in notify.CATEGORIES.items()]}

        @app.post("/api/notifications/prefs")
        def notifications_prefs(body: dict, who=Depends(auth)):
            uid, _ = who
            cats = body.get("categories") if isinstance(body.get("categories"), dict) else None
            quiet = body.get("quiet") if isinstance(body.get("quiet"), dict) else None
            return {"prefs": notify.set_prefs(uid, cats, quiet)}

        @app.post("/api/notifications/test")
        def notifications_test(who=Depends(auth)):
            uid, device = who
            if not device.get("push"):
                raise HTTPException(400, "Turn notifications on for this phone first.")
            if pcbridge.local_pc() and os.getenv("JARVIS_PUSH", "on").lower() == "off":
                notify.forward_to_server("Jarvis", "Test notification: you'll hear from me here.", uid, "jarvis", True)
                return {"sent": True}
            n = push("Jarvis", "Test notification: you'll hear from me here.", uid, "jarvis", urgent=True)
            return {"sent": n > 0}

        @app.post("/api/notify")
        def notify_relay(body: dict, who=Depends(auth)):
            """The PC hands a notification to the always-on server, which delivers it to phones."""
            owner_only(who[0])
            title, text = str(body.get("title", "Jarvis"))[:120], str(body.get("body", ""))[:600]
            uid = body.get("uid") if body.get("uid") in users.all_users() else None
            if not text:
                raise HTTPException(400, "Empty notification.")
            extra = clean_push_extra(body.get("extra"))   # 2026-11-10 C3: whitelisted keys, a mission url or nothing
            if extra:
                notify.notify(title, text, uid, str(body.get("category") or "jarvis"), bool(body.get("urgent")), extra=extra)
            else:
                notify.notify(title, text, uid, str(body.get("category") or "jarvis"), bool(body.get("urgent")))
            return {"ok": True}

        # ---- Mark VI missions: the queue lives here, on the always-on laptop (owner's decision 2026-11-10) ----------
        def relayed_for_owner(who, for_uid) -> None:
            """2026-11-10 review: the PC's token is the OWNER's (it is the mission worker device), but the PC also speaks for
            whoever is talking to it. A queue/list/cancel/pause from that device counts only when the PC says it is relaying
            the owner himself (`for_uid` = the token's own uid); a household member at the PC never acts on his missions."""
            if is_mission_worker(who[1]) and str(for_uid or "") != str(who[0]):
                raise HTTPException(403, "Missions belong to the owner; only he can queue, check, cancel or pause them.")

        @app.get("/api/missions")
        def missions_list(active: str = "", for_uid: str = "", who=Depends(auth)):
            from core import missions
            relayed_for_owner(who, for_uid)
            with users.acting_as(who[0]):
                return {"missions": missions.listing(active_only=active == "1", limit=20), "paused": missions.paused(),
                        "hold": missions.hold_reason()}

        @app.post("/api/missions")
        def missions_add(body: dict, who=Depends(auth)):
            from core import missions
            relayed_for_owner(who, body.get("for_uid"))
            with users.acting_as(who[0]):
                try:
                    return {"mission": missions.add(str(body.get("goal") or ""))}
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None

        @app.post("/api/missions/{mid}/cancel")
        def missions_cancel(mid: str, body: dict = Body(None), who=Depends(auth)):
            from core import missions
            relayed_for_owner(who, (body or {}).get("for_uid"))
            with users.acting_as(who[0]):
                try:
                    return {"mission": missions.cancel(mid)}
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None

        @app.post("/api/missions/pause")
        def missions_pause(body: dict, who=Depends(auth)):
            from core import missions
            owner_only(who[0])
            relayed_for_owner(who, body.get("for_uid"))
            return {"paused": missions.set_paused(bool(body.get("paused")))}

        @app.post("/api/missions/hold")
        def missions_hold(body: dict, who=Depends(auth)):
            """His words 'I'm at the PC' / 'I'm gonna game' hold missions; 'I'm done' lifts the hold (owner 2026-11-10)."""
            from core import missions
            owner_only(who[0])
            relayed_for_owner(who, body.get("for_uid"))
            if body.get("busy") is True:                       # a real yes only: "false" as text is not busy
                return {"hold": missions.set_hold(str(body.get("reason") or "")), "paused": missions.paused_switch()}
            missions.clear_hold()
            return {"hold": "", "paused": missions.paused_switch()}

        @app.post("/api/missions/{mid}/answer")
        def missions_answer(mid: str, body: dict, who=Depends(auth)):
            """His answer to a step a mission parked for approval (it waits until he answers; no answer is no)."""
            from core import missions
            owner_only(who[0])
            if is_mission_worker(who[1]):   # 2026-11-10 C2: the PC's token is an owner token, but it never approves its own step
                raise HTTPException(403, "The worker cannot answer its own approval.")
            with users.acting_as(who[0]):
                try:
                    return {"mission": missions.answer_approval(mid, bool(body.get("approved")), str(body.get("fp") or ""))}
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None

        # ---- Mark VI C2 (2026-11-10): the PC worker's side of the queue. Only the worker device may use these, so a phone
        # can never claim or finish runs; with JARVIS_MISSION_WORKER_DEVICE unset nobody may (refuse, never allow everyone).
        def is_mission_worker(device: dict) -> bool:
            wid = os.getenv("JARVIS_MISSION_WORKER_DEVICE", "").strip()
            return bool(wid) and str(device.get("id", "")) == wid

        def worker_only(who) -> None:
            owner_only(who[0])
            if not is_mission_worker(who[1]):
                raise HTTPException(403, "Only the mission worker may do that.")

        def worker_call(who, fn):
            worker_only(who)
            with users.acting_as(who[0]):
                try:
                    return fn()
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None

        @app.post("/api/missions/worker/claim")
        def missions_worker_claim(body: dict, who=Depends(auth)):
            from core import missions
            machine = str(body.get("machine") or "pc")[:20]
            return worker_call(who, lambda: {"mission": missions.claim_next(machine), "paused": missions.paused()})

        @app.post("/api/missions/worker/{run}/heartbeat")
        def missions_worker_heartbeat(run: str, body: dict, who=Depends(auth)):
            from core import missions
            return worker_call(who, lambda: missions.heartbeat(run, str(body.get("progress") or ""), _checkpoint(body)))

        def _checkpoint(body: dict):
            cp = body.get("checkpoint")
            if cp is not None and not isinstance(cp, dict):
                raise ValueError("A checkpoint is an object.")
            return cp

        # 2026-11-10 Mark VI C3: the LAPTOP sends the mission's phone report, where the state change commits - never the PC
        # (its tray balloon and HUD bubble would pop up over a game). Only when this call really changed the mission, after
        # the commit, never retried: a lost report beats a duplicate.
        mission_titles = {"approval": "Needs your OK", "done": "Mission done", "blocked": "Mission blocked", "failed": "Mission failed"}

        def repeated_failure(mid: str, run: str, kind: str, detail: str) -> bool:
            """The same failure as the previous run within the hour: he already heard it once."""
            from core import missions
            ids = [r["id"] for r in missions.runs(mid)]
            if kind not in ("failed", "blocked") or run not in ids or ids.index(run) == 0:
                return False
            prev = missions.runs(mid)[ids.index(run) - 1]
            return (prev.get("outcome") == kind and (prev.get("detail") or "")[:80] == detail[:80]
                    and time.time() - (prev.get("ended") or 0) < 3600)

        def mission_report(m: dict, run: str, kind: str, detail: str) -> None:
            if not m.get("changed") or repeated_failure(m["id"], run, kind, detail):
                return
            mid = m["id"]
            try:
                notify.notify(mission_titles[kind], f"{m['goal'][:80]}: {detail[:300]}", m["uid"], "missions",
                              urgent=(kind == "approval"),          # a parked approval waits forever on no answer: it must pass quiet hours
                              extra={"kind": kind, "mission": mid, "tag": "mission-" + mid, "url": "/#mission=" + mid})
            except Exception as error:  # noqa: BLE001
                diag.swallowed("mission push", error)

        def save_evidence(m: dict, run: str, body: dict) -> None:
            """A picture of the target window (never the whole screen), shown in the phone's mission sheet through the
            authenticated /api/file/u/missions/<name>. Never in the push itself (no auth there, and a 4 KB payload)."""
            import re
            from core import missions
            ev = body.get("evidence")
            b64 = ev.get("jpeg_b64") if isinstance(ev, dict) else None
            if not m.get("changed") or not isinstance(b64, str) or len(b64) > MAX_EVIDENCE * 4 // 3 + 8 \
                    or not re.fullmatch(r"[0-9a-f]{12}", run):
                return
            try:
                raw = base64.b64decode(b64, validate=True)
            except ValueError:
                return
            if len(raw) > MAX_EVIDENCE or not raw.startswith(b"\xff\xd8"):
                return
            name = f"{m['id']}-{run}.jpg"
            folder = storage.DATA / "users" / m["uid"] / "missions"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
            missions.set_result_image(m["id"], name)
            m["result_image"] = name

        @app.post("/api/missions/worker/{run}/park")
        def missions_worker_park(run: str, body: dict, who=Depends(auth)):
            from core import missions

            def park():
                if not isinstance(body.get("step"), dict):
                    raise ValueError("A parked step is an object.")
                preview = str(body.get("preview") or "")
                m = missions.park_for_approval(run, body["step"], preview, _checkpoint(body))
                save_evidence(m, run, body)
                mission_report(m, run, "approval", preview)
                return {"mission": m}
            return worker_call(who, park)

        @app.post("/api/missions/worker/{run}/finish")
        def missions_worker_finish(run: str, body: dict, who=Depends(auth)):
            from core import missions

            def finish():
                outcome, detail = str(body.get("outcome") or ""), str(body.get("detail") or "")
                m = missions.finish(run, outcome, detail)
                save_evidence(m, run, body)
                mission_report(m, run, outcome, detail)
                return {"mission": m}
            return worker_call(who, finish)

        @app.post("/api/missions/worker/{run}/release")
        def missions_worker_release(run: str, body: dict, who=Depends(auth)):
            """The PC has to stop for now (a game, he came back): back to the queue with its checkpoint, not a retry."""
            from core import missions
            return worker_call(who, lambda: {"mission": missions.release(run, _checkpoint(body))})

        @app.post("/api/missions/worker/{run}/approval")
        def missions_worker_approval(run: str, body: dict, who=Depends(auth)):
            """Mark VI C11: the step the owner approved, handed to the worker exactly once (stamped used), for the
            mission's current run only. The worker still checks its fingerprint before running it."""
            from core import missions
            return worker_call(who, lambda: {"approval": missions.consume_approval_for_run(run)})

        @app.get("/api/timers")
        def timers_list(who=Depends(auth)):
            from core import timers
            return {"timers": timers.active()}

        @app.post("/api/timers")
        def timers_add(body: dict, who=Depends(auth)):
            from core import timers
            from tools import system
            owner_only(who[0])
            try:
                seconds = int(body.get("seconds") or 0)
            except (TypeError, ValueError):
                seconds = 0
            if not 1 <= seconds <= 86400:
                raise HTTPException(400, "Timers run from one second to one day.")
            label = str(body.get("label") or "timer")[:60]
            timers.add(seconds, label, lambda name: system.timer_done(name, who[0]))
            return {"timers": timers.active()}

        @app.post("/api/timers/cancel")
        def timers_cancel(body: dict, who=Depends(auth)):
            from core import timers
            owner_only(who[0])
            return {"cancelled": timers.cancel(str(body.get("which") or "")), "timers": timers.active()}

        @app.get("/api/focus")
        def focus_get(who=Depends(auth)):
            from core import focus
            return focus.status()

        @app.post("/api/focus")
        def focus_set(body: dict, who=Depends(auth)):
            from core import focus
            owner_only(who[0])
            action = body.get("action")
            if action == "start":
                return focus.start(body.get("focus_minutes") or 25, body.get("break_minutes") or 5, body.get("rounds") or 4)
            if action == "stop":
                focus.stop()
                return focus.status()
            if action == "skip":
                return focus.skip()
            raise HTTPException(400, "action: start, stop or skip")

        @app.get("/api/countdowns")
        def countdowns_get(who=Depends(auth)):
            from tools import life
            with users.acting_as(who[0]):
                return {"countdowns": life.countdowns()}

        @app.post("/api/countdowns")
        def countdowns_add(body: dict, who=Depends(auth)):
            from tools import life
            with users.acting_as(who[0]):
                msg = life.add_countdown(str(body.get("title", ""))[:60], str(body.get("date", "")))
                return {"message": msg, "countdowns": life.countdowns()}

        @app.post("/api/countdowns/remove")
        def countdowns_remove(body: dict, who=Depends(auth)):
            from tools import life
            with users.acting_as(who[0]):
                rows = [r for r in storage.load(life.COUNTDOWNS, []) if r.get("id") != body.get("id")]
                storage.save(life.COUNTDOWNS, rows)
                return {"countdowns": life.countdowns()}

        @app.get("/api/weekly")
        def weekly_get(who=Depends(auth)):
            from tools import life
            with users.acting_as(who[0]):
                return life.weekly()

        @app.get("/api/lists")
        def lists_get(who=Depends(auth)):
            from tools import notes
            with users.acting_as(who[0]):
                rows = [n for n in notes._all_notes() if not n.get("done") and n.get("list") in ("todo", "shopping")]
            return {"items": rows}

        @app.post("/api/lists/add")
        def lists_add(body: dict, who=Depends(auth)):
            from tools import notes
            text, name = str(body.get("text", "")).strip()[:200], body.get("list")
            if not text or name not in ("todo", "shopping"):
                raise HTTPException(400, "Say what to add, to the to-do or shopping list.")
            with users.acting_as(who[0]):
                notes.add_note(text, name)
                return {"items": [n for n in notes._all_notes() if not n.get("done") and n.get("list") in ("todo", "shopping")]}

        @app.post("/api/lists/done")
        def lists_done(body: dict, who=Depends(auth)):
            from tools import notes
            ids = [str(i) for i in (body.get("ids") or [])][:50]
            with users.acting_as(who[0]):
                notes.update_list(ids, "done")
                return {"items": [n for n in notes._all_notes() if not n.get("done") and n.get("list") in ("todo", "shopping")]}

        @app.get("/api/media")
        def media_get(who=Depends(auth)):
            owner_only(who[0])
            if not pcbridge.local_pc():
                return {"available": False}
            from tools import media
            return {"available": True, **server._cached("media", 2, media.state)}

        @app.post("/api/media")
        def media_set(body: dict, who=Depends(auth)):
            owner_only(who[0])
            if not pcbridge.local_pc():
                raise HTTPException(503, "Media controls work on the PC.")
            from tools import media
            if body.get("action") in ("play_pause", "next", "previous", "mute"):
                media.media_control(body["action"])
            elif body.get("volume") is not None:
                media.set_volume(int(body["volume"]))
            else:
                raise HTTPException(400, "action or volume")
            server._cache.pop("media", None)
            return {"available": True, **media.state()}

        @app.get("/api/dashboard")
        def dashboard(who=Depends(auth)):
            """Everything the TV dashboard shows, refreshed every few seconds."""
            uid, _ = who
            return {"jarvis": hud.feed(), "homelab": server._cached("homelab", 30, server._homelab),
                    "weather": server._cached("weather", 900, server._weather),
                    "playing": server._cached("playing", 15, server._playing),
                    "problems": list(storage.load_global("homelab_watch.json", {}).get("problems", {}).values())
                                + link_problems_cached(),
                    "today": server._today(uid), "time": datetime.now().strftime("%H:%M"),
                    "date": datetime.now().strftime("%A %d %B")}

        # Compress the public app/artwork only. API, audio and private file routes
        # stay outside this mount; moderate compression avoids slow laptop loads.
        app.mount("/", GZipMiddleware(_revalidate_pages(StaticFiles(directory=APP_DIR, html=True)),
                                    minimum_size=4096, compresslevel=4), name="app")
        return app
