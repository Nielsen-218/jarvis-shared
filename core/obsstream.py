"""OBS control for the Streaming protocol (owner 2026-11-20: "I will stream on all platforms Kick YouTube TikTok and Twitch").

OBS Studio's own remote control (obs-websocket 5, built into OBS 28+) on 127.0.0.1. The password is the one OBS generated
in its own settings file (%APPDATA%/obs-studio/plugin_config/obs-websocket/config.json): read here, never typed, logged or
said. The extra platforms are Aitum Multistream outputs (vendor "aitum-multistream": get_outputs / start_output /
stop_output, each an OBS output named "aitum_multi_output_<name>"); the main platform is OBS's own Stream settings.

Nothing goes live by itself: the Streaming protocol only gets OBS ready ("stream_ready"). His words start and end the
broadcast: "go live", "end the stream", "am I live". A platform is called live only once OBS reports its output active.
From the phone (the laptop) the sentence goes to the PC, where OBS runs."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import struct
import time
import uuid
from pathlib import Path

PORT = 4455
CONNECT_S = 3.0
READY_WAIT_S = 15.0       # OBS just opened by the protocol takes a few seconds before its remote control answers
LIVE_WAIT_S = 20.0        # an RTMP output connects in 1-10 s; longer is reported as "not confirmed"
END_WAIT_S = 10.0
POLL_S = 1.0
SLOW_S = 10.0             # start/stop requests: Aitum clicks its own button on OBS's UI thread
VENDOR = "aitum-multistream"
OUTPUT_PREFIX = "aitum_multi_output_"
SETUP = ("In OBS open Tools, then WebSocket Server Settings, and tick Enable WebSocket server; I read its password "
         "myself, you don't need to tell me")


class OBSError(Exception):
    """A plain-words reason OBS could not be reached or did not do it."""


def _config_path() -> Path:
    return Path(os.path.expandvars(r"%APPDATA%")) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"


def settings() -> dict:
    """{'enabled', 'port', 'password'} from OBS's own file; empty values when OBS never ran."""
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"enabled": bool(data.get("server_enabled")), "port": int(data.get("server_port") or PORT),
            "password": str(data.get("server_password") or "") if data.get("auth_required", True) else ""}


def obs_running() -> bool:
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == "obs64.exe" for p in psutil.process_iter(["name"]))
    except Exception:  # noqa: BLE001 - unknown: the connection attempt says what is wrong
        return True


# -- a minimal WebSocket client (RFC 6455): text frames to a local server, nothing else ------------------------------------

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _Socket:
    def __init__(self, port: int, timeout: float = CONNECT_S, host: str = "127.0.0.1"):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        self.dead = False         # after a timeout mid-frame the stream is out of step: never read from it again
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           f"Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OBSError("OBS closed the connection before it answered")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        want = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        fields = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if " 101 " not in lines[0] + " " or fields.get("sec-websocket-accept") != want:
            raise OBSError("something other than OBS answered on its remote-control port")

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            if self.dead:
                raise OBSError("OBS stopped answering")
            try:
                chunk = self.sock.recv(65536)
            except (socket.timeout, TimeoutError):
                self.dead = True
                raise OBSError("OBS stopped answering") from None
            except OSError as e:
                self.dead = True
                raise OBSError(f"the connection to OBS broke ({type(e).__name__})") from None
            if not chunk:
                self.dead = True
                raise OBSError("OBS closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, obj: dict) -> None:
        if self.dead:
            raise OBSError("OBS stopped answering")
        try:
            self._frame(0x1, json.dumps(obj).encode())
        except OSError as e:
            raise OBSError(f"the connection to OBS broke ({type(e).__name__})") from None

    def recv(self) -> dict:
        message = b""
        while True:
            b0, b1 = self._exact(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exact(8))[0]
            mask = self._exact(4) if b1 & 0x80 else b""
            data = self._exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 0
                raise OBSError("OBS refused the password in its own settings file" if code == 4009 else
                               f"OBS closed the connection (code {code})")
            if opcode == 0x9:
                self._frame(0xA, data)
                continue
            if opcode in (0x1, 0x2, 0x0):
                message += data
                if b0 & 0x80:
                    return json.loads(message.decode("utf-8"))

    def close(self) -> None:
        try:
            self._frame(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Client:
    """One identified obs-websocket session: request(type, data) -> responseData, or OBSError with OBS's own comment."""

    def __init__(self, port: int, password: str, timeout: float = CONNECT_S):
        self.ws = _Socket(port, timeout)
        try:
            hello = self.ws.recv()
            if hello.get("op") != 0:
                raise OBSError("OBS didn't greet as expected")
            ident = {"rpcVersion": 1, "eventSubscriptions": 0}
            auth = (hello.get("d") or {}).get("authentication")
            if auth:
                if not password:
                    raise OBSError("OBS asks for a password but its settings file has none")
                secret = base64.b64encode(hashlib.sha256((password + auth["salt"]).encode()).digest()).decode()
                ident["authentication"] = base64.b64encode(hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
            self.ws.send({"op": 1, "d": ident})
            while True:
                msg = self.ws.recv()
                if msg.get("op") == 2:
                    break
        except Exception:
            self.ws.close()
            raise

    @property
    def dead(self) -> bool:
        return self.ws.dead

    def request(self, kind: str, data: dict | None = None, timeout: float | None = None) -> dict:
        if timeout is not None:
            self.ws.sock.settimeout(timeout)
        try:
            return self._request(kind, data)
        finally:
            if timeout is not None and not self.ws.dead:
                self.ws.sock.settimeout(CONNECT_S)

    def _request(self, kind: str, data: dict | None) -> dict:
        rid = uuid.uuid4().hex
        body = {"requestType": kind, "requestId": rid}
        if data is not None:
            body["requestData"] = data
        self.ws.send({"op": 6, "d": body})
        while True:
            msg = self.ws.recv()
            d = msg.get("d") or {}
            if msg.get("op") == 7 and d.get("requestId") == rid:
                status = d.get("requestStatus") or {}
                if not status.get("result"):
                    raise OBSError(str(status.get("comment") or f"OBS said no to {kind} (code {status.get('code')})"))
                return d.get("responseData") or {}

    def vendor(self, request: str, data: dict | None = None, timeout: float | None = None) -> dict:
        out = self.request("CallVendorRequest", {"vendorName": VENDOR, "requestType": request, "requestData": data or {}},
                           timeout)
        return out.get("responseData") or {}

    def close(self) -> None:
        self.ws.close()


def connect(wait: float = 0.0) -> Client:
    """A session with the running OBS, or OBSError saying in plain words what he has to do. `wait`: keep trying that long."""
    deadline = time.monotonic() + wait
    while True:
        if not obs_running():
            error = OBSError("OBS isn't open")
        else:
            conf = settings()
            try:
                return Client(conf["port"], conf["password"])
            except (ConnectionRefusedError, socket.timeout, TimeoutError) as e:
                error = OBSError("OBS's remote control is off. " + SETUP if not conf["enabled"] else
                                 f"OBS didn't answer on its remote-control port ({type(e).__name__})")
            except OSError as e:
                error = OBSError(f"I couldn't reach OBS ({e.__class__.__name__})")
            except OBSError as e:
                if "password" in str(e):
                    raise
                error = e
        if time.monotonic() >= deadline:
            raise error
        time.sleep(POLL_S)


# -- what is set up, what is live ----------------------------------------------------------------------------------------
# An output's state is True (OBS reports it active), False (OBS reports it inactive) or None (OBS didn't say): None is
# never said as "off" or "ended" (review 2026-11-20: a failed status query read as "not live" while he was broadcasting).

_SERVICE_NAMES = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick", "tiktok": "TikTok", "facebook": "Facebook"}


def _platform(text: str) -> str:
    """A platform's name; a custom server is never read out (its address can hold the stream key: rtmp://host/app/KEY)."""
    low = (text or "").lower()
    for key, nice in _SERVICE_NAMES.items():
        if key in low:
            return nice
    if not text or "/" in text or ":" in text or "?" in text:
        return "your custom server"
    return text.strip()


def main_platform(c: Client) -> tuple[str, bool]:
    """(name, has_a_key) of OBS's own Stream settings. The key itself is never kept or said."""
    out = c.request("GetStreamServiceSettings")
    s = out.get("streamServiceSettings") or {}
    name = _platform(s.get("service") or s.get("server") or "")
    has_key = bool(s.get("key") or s.get("bearer_token"))
    return name, has_key


def extra_outputs(c: Client) -> list[dict]:
    """Aitum Multistream's outputs: [{'name', 'vertical'}]; [] only when OBS says the add-on isn't there. Any other failure
    raises: 'end the stream' must not report the main platform ended while it never reached the others."""
    try:
        out = c.vendor("get_outputs")
    except OBSError as e:
        if "vendor" in str(e).lower():
            return []
        raise OBSError(f"I couldn't read the Aitum Multistream platforms ({str(e).rstrip('.')})") from None
    return [{"name": str(o.get("name")), "vertical": bool(o.get("vertical"))} for o in out.get("outputs") or [] if o.get("name")]


def _status(c: Client, output: str | None) -> dict | None:
    try:
        return c.request("GetStreamStatus") if output is None else c.request("GetOutputStatus", {"outputName": OUTPUT_PREFIX + output})
    except OBSError:
        return None


def _on(c: Client, output: str | None) -> bool | None:
    s = _status(c, output)
    return None if s is None else bool(s.get("outputActive"))


def _label(o: dict) -> str:
    return o["name"] + (" (vertical)" if o.get("vertical") else "")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _are(items: list[str]) -> str:
    return "is" if len(items) == 1 else "are"


def _lineup(c: Client) -> tuple[str, bool, list[dict]]:
    name, has_key = main_platform(c)
    return name, has_key, extra_outputs(c)


def _usable(c: Client) -> Client:
    """The same session, or a new one when a timeout left it out of step."""
    if not c.dead:
        return c
    c.close()
    return connect()


def ready_text(wait: float | None = None) -> tuple[bool, str]:
    """The Streaming protocol's check: is OBS reachable, and where would 'go live' send the stream. Reads only."""
    try:
        c = connect(READY_WAIT_S if wait is None else wait)
    except OBSError as e:
        return False, str(e).rstrip(".")
    try:
        name, has_key, extras = _lineup(c)
        if _on(c, None):
            return True, f"you're already live on {name}"
    except OBSError as e:
        return False, f"OBS answered but {str(e).rstrip('.')}"
    finally:
        c.close()
    targets = ([name] if has_key else []) + [_label(o) for o in extras]
    if not targets:
        return False, ("OBS is open, but no platform is set up yet: sign in under Settings, Stream, and add the others in "
                       "the Aitum Multistream panel")
    unset = f"; {name} has no stream key in OBS's Stream settings yet" if not has_key else ""
    return True, f"OBS is ready to stream to {_join(targets)}{unset}. Say 'go live' when you are; nothing is broadcast before that"


def _wait_for(c: Client, outputs: list, want: bool, wait: float) -> dict:
    """{output: True/False/None} once OBS reports every output in the wanted state, or after `wait` seconds."""
    deadline = time.monotonic() + wait
    while True:
        state = {o: _on(c, o) for o in outputs}
        if all(v is want for v in state.values()) or time.monotonic() >= deadline:
            return state
        if c.dead:
            try:
                c = _usable(c)
            except OBSError:
                return {o: None for o in outputs}
        time.sleep(POLL_S)


def go_live(wait: float | None = None) -> str:
    try:
        c = connect()
    except OBSError as e:
        return f"I can't go live: {str(e).rstrip('.')}."
    started: list = []
    problems: list[str] = []
    try:
        try:
            name, has_key, extras = _lineup(c)
        except OBSError as e:
            return f"I didn't start anything: {str(e).rstrip('.')}."
        labels = {None: name, **{o["name"]: _label(o) for o in extras}}
        try:
            if _on(c, None):
                started.append(None)
            elif has_key:
                try:
                    c.request("StartStream", timeout=SLOW_S)
                    started.append(None)
                except OBSError as e:
                    if c.dead:
                        started.append(None)          # no answer: it may have started; OBS's own state decides below
                    else:
                        problems.append(f"{name} didn't start ({str(e).rstrip('.')})")
            else:
                problems.append(f"{name} has no stream key in OBS's Stream settings")
            for o in extras:
                c = _usable(c)
                if _on(c, o["name"]):
                    started.append(o["name"])
                    continue
                try:
                    answer = c.vendor("start_output", {"name": o["name"]}, SLOW_S)
                    if answer.get("success") is False:
                        raise OBSError(answer.get("error") or "refused")
                    started.append(o["name"])
                except OBSError as e:
                    if c.dead:
                        started.append(o["name"])
                    else:
                        problems.append(f"{_label(o)} didn't start ({str(e).rstrip('.')})")
        except Exception as e:  # noqa: BLE001 - something already started may be live: still checked and said below
            problems.append(f"I stopped partway ({str(e).strip().rstrip('.') or type(e).__name__})")
        if not started:
            return "Nothing went live: " + "; ".join(problems) + "."
        try:
            c = _usable(c)
            state = _wait_for(c, started, True, LIVE_WAIT_S if wait is None else wait)
        except OBSError:
            state = {o: None for o in started}
    finally:
        c.close()
    live = [labels[o] for o, on in state.items() if on is True]
    late = [labels[o] for o, on in state.items() if on is not True]
    parts = []
    if live:
        parts.append(f"You're live on {_join(live)}.")
    if late:
        parts.append(f"{_join(late)} {_are(late)} not confirmed live yet; OBS hasn't reported "
                     f"{'it' if len(late) == 1 else 'them'} connected.")
    if problems:
        parts.append(" ".join(p[0].upper() + p[1:] + "." for p in problems))
    return " ".join(parts)


def end(wait: float | None = None) -> str:
    try:
        c = connect()
    except OBSError as e:
        return f"I can't reach OBS to end the stream: {str(e).rstrip('.')}."
    try:
        try:
            name, _has_key, extras = _lineup(c)
        except OBSError as e:
            return f"I couldn't end the stream: {str(e).rstrip('.')}. End it in OBS."
        labels = {None: name, **{o["name"]: _label(o) for o in extras}}
        before = {o: _on(c, o) for o in labels}
        on = [o for o, v in before.items() if v is not False]          # live, or unknown: stopped too, to be sure
        if not on:
            return "You're not live anywhere, so there was nothing to end."
        for o in on:
            try:
                c = _usable(c)
                if o is None:
                    c.request("StopStream", timeout=SLOW_S)
                else:
                    c.vendor("stop_output", {"name": o}, SLOW_S)
            except OBSError:
                pass                      # judged below by what OBS reports, not by the request
        try:
            c = _usable(c)
            state = _wait_for(c, on, False, END_WAIT_S if wait is None else wait)
        except OBSError:
            state = {o: None for o in on}
    finally:
        c.close()
    off = [labels[o] for o, v in state.items() if v is False and before[o] is True]
    still = [labels[o] for o, v in state.items() if v is True]
    unknown = [labels[o] for o, v in state.items() if v is None]
    parts = [f"Stream ended on {_join(off)}."] if off else []
    if still:
        parts.append(f"{_join(still)} {_are(still)} still live: end {'it' if len(still) == 1 else 'them'} in OBS.")
    if unknown:
        parts.append(f"I couldn't read whether {_join(unknown)} stopped: check OBS.")
    return " ".join(parts) or "You're not live anywhere, so there was nothing to end."


def _clock(ms: int) -> str:
    minutes = int(ms or 0) // 60000
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def status() -> str:
    try:
        c = connect()
    except OBSError as e:
        return f"I can't check the stream: {str(e).rstrip('.')}."
    try:
        try:
            name, _has_key, extras = _lineup(c)
        except OBSError as e:
            return f"I can't check the stream: {str(e).rstrip('.')}."
        main = _status(c, None)
        rows = [(name, main)] + [(_label(o), _status(c, o["name"])) for o in extras]
    finally:
        c.close()
    live = [(n, s) for n, s in rows if s is not None and s.get("outputActive")]
    unknown = [n for n, s in rows if s is None]
    off = [n for n, s in rows if s is not None and not s.get("outputActive")]
    if not live:
        if not unknown:
            return "You're not live right now."
        if off:
            return f"You're not live on {_join(off)}; I couldn't read {_join(unknown)}: check OBS."
        return "I couldn't read the stream's state from OBS: check OBS."
    parts = [f"You're live on {_join([n for n, _ in live])}"]
    if main and main.get("outputActive"):
        parts[0] += f", {_clock(main.get('outputDuration'))} in"
        total, skipped = int(main.get("outputTotalFrames") or 0), int(main.get("outputSkippedFrames") or 0)
        if total and skipped / total >= 0.01:
            parts.append(f"OBS skipped {100 * skipped / total:.0f}% of frames, so the PC is struggling to keep up")
    shaky = [n for n, s in live if s.get("outputReconnecting")]
    if shaky:
        parts.append(f"{_join(shaky)} {_are(shaky)} reconnecting")
    if off:
        parts.append(f"not live on {_join(off)}")
    if unknown:
        parts.append(f"I couldn't read {_join(unknown)}")
    return "; ".join(parts) + "."


def still_live() -> bool:
    """For 'stop streaming protocol': True when anything is (or may be) live, so the reminder is said."""
    try:
        c = connect()
    except OBSError:
        return False
    try:
        outputs = [None] + [o["name"] for o in extra_outputs(c)]
        return any(_on(c, o) is not False for o in outputs)
    except OBSError:
        return True
    finally:
        c.close()


# -- his words ----------------------------------------------------------------------------------------------------------

_LEAD = r"^\W*(?:(?:ok|okay|hey|jarvis|please|alright|right|so|now|let'?s)[\s,]+)*"
_END = r"(?:[\s,]+(?:now|please|sir|jarvis|for me|right now))*\W*$"
_ALL = r"(?:\s+(?:on\s+)?(?:all|every)(?:\s+(?:four|4))?(?:\s+(?:platforms?|of them|sites))?|\s+everywhere)?"
_STREAM = r"(?:the |my |our )?(?:stream|streaming|broadcast|live ?stream)"
_GO = re.compile(_LEAD + rf"(?:(?:can|could|will) you\s+)?(?:go live|start {_STREAM}|start streaming|begin {_STREAM}){_ALL}{_END}", re.I)
_STOP = re.compile(_LEAD + rf"(?:(?:can|could|will) you\s+)?(?:(?:end|stop|finish|kill|close) {_STREAM}|stop streaming|go offline|"
                   rf"end it all){_ALL}{_END}", re.I)
_ASK = re.compile(_LEAD + rf"(?:am i live|are (?:we|you) live|is {_STREAM} (?:live|on|running|ok|okay|healthy|going|good)|"
                  rf"(?:check )?(?:the |my )?stream (?:status|health)|how(?:'s| is) {_STREAM}(?: going| doing| looking)?)"
                  rf"(?:\s+(?:right now|now|sir|jarvis))?\W*$", re.I)


def kind(raw: str) -> str | None:
    text = (raw or "").replace("’", "'").strip()
    if not text or len(text) > 120:
        return None
    if _ASK.match(text):
        return "status"
    if _STOP.match(text):
        return "end"
    if _GO.match(text):
        return "go"
    return None


def handle(raw: str) -> str | None:
    """'go live' / 'end the stream' / 'am I live' -> the answer; None for every other sentence."""
    what = kind(raw)
    if what is None:
        return None
    from core import pcbridge, protocols
    if not protocols._is_owner():
        return "Only the PC's owner can start, end or check the stream."
    if not pcbridge.local_pc():
        from core import pcfast           # from the phone: OBS runs on the PC
        reply = pcfast.ask_pc(raw)
        return reply or ("I didn't get an answer from the PC, so I can't tell whether the stream "
                         + {"go": "started.", "end": "ended.", "status": "is live."}[what])
    try:
        return {"go": go_live, "end": end, "status": status}[what]()
    except Exception as error:  # noqa: BLE001 - his stream command is answered here, never handed to the model to guess
        word = {"go": "start the stream", "end": "end the stream", "status": "check the stream"}[what]
        return f"I couldn't {word}: {str(error).strip().rstrip('.') or type(error).__name__}. Check OBS itself."
