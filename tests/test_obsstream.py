"""core/obsstream against a fake OBS: a real local WebSocket server speaking obs-websocket 5 (hello, password check,
identify, requests) with Aitum Multistream's vendor requests. Nothing here reaches the real OBS (conftest)."""
import base64
import hashlib
import json
import socket
import struct
import threading

import pytest

from core import obsstream, protocols, quick

PASSWORD = "s3cret-from-obs"


class FakeOBS:
    def __init__(self, *, service="Twitch", key="live_123", outputs=("YouTube", "Kick", "TikTok"), vendor=True,
                 never_connects=(), password=PASSWORD, unreadable=(), outputs_error=False, slow=(), server=""):
        self.service, self.key, self.vendor_ok, self.password = service, key, vendor, password
        self.unreadable, self.outputs_error, self.slow, self.server = set(unreadable), outputs_error, set(slow), server
        self.outputs = [{"name": n, "vertical": n == "TikTok"} for n in outputs]
        self.active = {None: False, **{n: False for n in outputs}}
        self.never = set(never_connects)
        self.calls = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    # -- wire ------------------------------------------------------------------------------------------------------------
    def _serve(self):
        while not self.stop:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    @staticmethod
    def _read_frame(conn, buf):
        def need(n):
            nonlocal buf
            while len(buf) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out
        b0, b1 = need(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", need(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", need(8))[0]
        assert b1 & 0x80, "a client frame must be masked"
        mask = need(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(need(n)))
        return b0 & 0x0F, data, buf

    @staticmethod
    def _send(conn, obj=None, opcode=1, raw=b""):
        data = json.dumps(obj).encode() if obj is not None else raw
        n = len(data)
        head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n))
        conn.sendall(head + data)

    def _client(self, conn):
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += conn.recv(4096)
            head, buf = buf.split(b"\r\n\r\n", 1)
            key = next(ln.split(":", 1)[1].strip() for ln in head.decode().split("\r\n") if ln.lower().startswith("sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
            salt, challenge = "c2FsdA==", "Y2hhbGxlbmdl"
            self._send(conn, {"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": salt, "challenge": challenge}}})
            _op, data, buf = self._read_frame(conn, buf)
            ident = json.loads(data)["d"]
            secret = base64.b64encode(hashlib.sha256((self.password + salt).encode()).digest()).decode()
            if ident.get("authentication") != base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode():
                self._send(conn, opcode=8, raw=struct.pack("!H", 4009))
                return
            self._send(conn, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
            while True:
                op, data, buf = self._read_frame(conn, buf)
                if op == 8:
                    return
                d = json.loads(data)["d"]
                ok, out, comment = self._answer(d["requestType"], d.get("requestData") or {})
                self._send(conn, {"op": 7, "d": {"requestType": d["requestType"], "requestId": d["requestId"],
                                                 "requestStatus": {"result": ok, "code": 100 if ok else 600, "comment": comment},
                                                 "responseData": out}})
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()

    # -- OBS -------------------------------------------------------------------------------------------------------------
    def _answer(self, kind, data):
        self.calls.append((kind, data.get("requestType") or data.get("outputName") or ""))
        if kind in self.slow or data.get("requestType") in self.slow:
            import time
            time.sleep(0.6)                               # longer than the test's SLOW_S: the client gives up waiting
        if kind == "GetStreamServiceSettings":
            return True, {"streamServiceType": "rtmp_common",
                          "streamServiceSettings": {"service": self.service, "server": self.server, "key": self.key}}, ""
        if kind == "GetStreamStatus":
            return True, {"outputActive": self.active[None], "outputDuration": 3_720_000, "outputTotalFrames": 1000,
                          "outputSkippedFrames": 50, "outputReconnecting": False}, ""
        if kind == "GetOutputStatus":
            name = data["outputName"].removeprefix(obsstream.OUTPUT_PREFIX)
            if name not in self.active or name in self.unreadable:
                return False, {}, "No output was found"
            return True, {"outputActive": self.active[name], "outputReconnecting": False}, ""
        if kind == "StartStream":
            if self.active[None]:
                return False, {}, "The stream output is already active."
            self.active[None] = True
            return True, {}, ""
        if kind == "StopStream":
            self.active[None] = False
            return True, {}, ""
        if kind == "CallVendorRequest":
            if not self.vendor_ok:
                return False, {}, "No vendor was found by that name."
            req, name = data["requestType"], (data.get("requestData") or {}).get("name")
            if req == "get_outputs" and self.outputs_error:
                return False, {}, "The request timed out"
            if req == "get_outputs":
                return True, {"responseData": {"outputs": self.outputs, "success": True}}, ""
            if req in ("start_output", "stop_output"):
                if name not in self.active:
                    return True, {"responseData": {"success": False, "error": "Output not found"}}, ""
                if name not in self.never:
                    self.active[name] = req == "start_output"
                return True, {"responseData": {"success": True}}, ""
        return False, {}, f"unknown request {kind}"

    def close(self):
        self.stop = True
        self.srv.close()


@pytest.fixture
def obs(monkeypatch):
    made = []

    def start(**kw):
        fake = FakeOBS(**kw)
        made.append(fake)
        monkeypatch.setattr(obsstream, "obs_running", lambda: True)
        monkeypatch.setattr(obsstream, "settings", lambda: {"enabled": True, "port": fake.port, "password": PASSWORD})
        return fake
    monkeypatch.setattr(obsstream, "POLL_S", 0.05)
    monkeypatch.setattr(obsstream, "LIVE_WAIT_S", 0.5)
    monkeypatch.setattr(obsstream, "END_WAIT_S", 0.5)
    monkeypatch.setattr(obsstream, "READY_WAIT_S", 0.0)
    monkeypatch.setattr(obsstream, "SLOW_S", 0.2)
    yield start
    for f in made:
        f.close()


@pytest.fixture
def owner_pc(monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(protocols, "_is_owner", lambda uid=None: True)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)


# ---- the words ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("said", ["go live", "Go live.", "Jarvis, go live on all four", "let's go live", "start the stream",
                                  "start streaming", "go live everywhere", "Okay go live now", "can you start the stream please"])
def test_go_live_words(said):
    assert obsstream.kind(said) == "go"


@pytest.mark.parametrize("said", ["end the stream", "stop streaming", "Stop the stream.", "go offline", "end the stream on all platforms",
                                  "Jarvis, end my stream now"])
def test_end_words(said):
    assert obsstream.kind(said) == "end"


@pytest.mark.parametrize("said", ["am I live?", "Are we live", "how's the stream going?", "stream status", "is the stream okay?"])
def test_status_words(said):
    assert obsstream.kind(said) == "status"


@pytest.mark.parametrize("said", ["I'll go live later", "should I go live tonight?", "start streaming protocol", "stop streaming protocol",
                                  "I don't want to go live", "go live on twitch tomorrow", "what's a live stream", "start the stream of tasks",
                                  "streaming is fun", "tell me about going live"])
def test_other_sentences_are_not_stream_commands(said):
    assert obsstream.kind(said) is None


# ---- OBS ------------------------------------------------------------------------------------------------------------------

def test_go_live_starts_every_platform_and_says_what_is_live(obs, owner_pc):
    fake = obs()
    reply = quick.handle("go live")
    assert reply == "You're live on Twitch, YouTube, Kick and TikTok (vertical)."
    assert all(fake.active.values())


def test_a_platform_obs_never_reports_connected_is_not_called_live(obs, owner_pc):
    fake = obs(never_connects=("Kick",))
    reply = obsstream.go_live()
    assert reply.startswith("You're live on Twitch, YouTube and TikTok (vertical).")
    assert "Kick is not confirmed live yet" in reply
    assert not fake.active["Kick"]


def test_no_stream_key_on_the_main_platform_is_said_and_the_others_still_start(obs, owner_pc):
    fake = obs(key="")
    reply = obsstream.go_live()
    assert "You're live on YouTube, Kick and TikTok (vertical)." in reply and "Twitch has no stream key" in reply
    assert ("StartStream", "") not in fake.calls


def test_without_aitum_only_the_main_platform(obs, owner_pc):
    obs(vendor=False)
    assert obsstream.go_live() == "You're live on Twitch."


def test_nothing_set_up_goes_nowhere(obs, owner_pc):
    obs(key="", outputs=())
    assert obsstream.go_live() == "Nothing went live: Twitch has no stream key in OBS's Stream settings."


def test_going_live_twice_starts_nothing_again(obs, owner_pc):
    fake = obs()
    obsstream.go_live()
    fake.calls.clear()
    assert obsstream.go_live().startswith("You're live on Twitch, YouTube, Kick and TikTok")
    assert not [c for c in fake.calls if c[0] in ("StartStream",) or c[1] == "start_output"]


def test_end_the_stream_stops_everything(obs, owner_pc):
    fake = obs()
    obsstream.go_live()
    assert quick.handle("end the stream") == "Stream ended on Twitch, YouTube, Kick and TikTok (vertical)."
    assert not any(fake.active.values())
    assert obsstream.end() == "You're not live anywhere, so there was nothing to end."


def test_status_reports_time_skipped_frames_and_what_is_off(obs, owner_pc):
    fake = obs()
    fake.active.update({None: True, "YouTube": True})
    reply = obsstream.status()
    assert reply.startswith("You're live on Twitch and YouTube, 1 h 2 min in")
    assert "skipped 5% of frames" in reply and "not live on Kick and TikTok (vertical)" in reply


def test_status_when_off(obs, owner_pc):
    obs()
    assert quick.handle("am I live?") == "You're not live right now."


def test_a_wrong_password_is_said_plainly(obs, owner_pc, monkeypatch):
    fake = obs()
    monkeypatch.setattr(obsstream, "settings", lambda: {"enabled": True, "port": fake.port, "password": "old"})
    assert obsstream.go_live() == "I can't go live: OBS refused the password in its own settings file."


def test_obs_closed_or_remote_control_off(owner_pc, monkeypatch):
    assert obsstream.go_live() == "I can't go live: OBS isn't open."
    monkeypatch.setattr(obsstream, "obs_running", lambda: True)
    reply = obsstream.go_live()
    assert reply.startswith("I can't go live: OBS's remote control is off. In OBS open Tools, then WebSocket Server Settings")


def test_the_password_is_never_in_an_answer(obs, owner_pc):
    obs()
    for said in ("go live", "am I live", "end the stream"):
        assert PASSWORD not in quick.handle(said) and "live_123" not in quick.handle("am I live")


def test_not_the_owner(monkeypatch):
    monkeypatch.setattr(protocols, "_is_owner", lambda uid=None: False)
    assert obsstream.handle("go live") == "Only the PC's owner can start, end or check the stream."


def test_from_the_phone_the_pc_answers(monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(protocols, "_is_owner", lambda uid=None: True)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    asked = []
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: asked.append(raw) or "You're live on Twitch.")
    assert obsstream.handle("go live") == "You're live on Twitch." and asked == ["go live"]
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: None)
    assert obsstream.handle("end the stream") == "I didn't get an answer from the PC, so I can't tell whether the stream ended."


def test_an_unexpected_error_is_answered_not_handed_to_the_model(owner_pc, monkeypatch):
    monkeypatch.setattr(obsstream, "go_live", lambda: 1 / 0)
    assert obsstream.handle("go live").startswith("I couldn't start the stream: division by zero")


# ---- the protocol ---------------------------------------------------------------------------------------------------------

def test_the_streaming_protocol_never_goes_live():
    steps = [s["do"] for s in protocols.STREAMING["steps"]]
    assert steps == ["free_up", "close_tab", "open_app", "open_app", "open_app", "stream_ready", "open_app"]
    assert [s.get("target") for s in protocols.STREAMING["steps"] if s["do"] == "open_app"] == \
        ["animaze", "obs studio", "discord", "apex legends"]
    assert protocols.find("streaming") is protocols.STREAMING or protocols.find("streaming")["name"] == "streaming"


def test_stream_ready_step_lists_where_it_would_go(obs, owner_pc):
    fake = obs()
    status, detail = protocols._stream_ready({"do": "stream_ready"}, {})
    assert status == protocols.DONE
    assert detail == ("OBS is ready to stream to Twitch, YouTube, Kick and TikTok (vertical). Say 'go live' when you are; "
                      "nothing is broadcast before that")
    assert not any(fake.active.values()) and not [c for c in fake.calls if c[0] == "StartStream" or c[1] == "start_output"]


def test_stream_ready_says_what_to_do_when_obs_cannot_be_reached(owner_pc, monkeypatch):
    monkeypatch.setattr(obsstream, "obs_running", lambda: True)
    monkeypatch.setattr(obsstream, "READY_WAIT_S", 0.0)
    status, detail = protocols._stream_ready({"do": "stream_ready"}, {})
    assert status == protocols.FAILED and "Tools, then WebSocket Server Settings" in detail


def test_stream_ready_on_the_laptop_is_skipped(monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    assert protocols._stream_ready({"do": "stream_ready"}, {})[0] == protocols.SKIPPED


def test_stopping_the_protocol_while_live_does_not_end_the_broadcast(obs, owner_pc, monkeypatch):
    from core import gameboost
    fake = obs()
    fake.active[None] = True
    monkeypatch.setattr(gameboost, "active", lambda: False)
    reply = protocols.handle("stop streaming protocol")
    assert reply == "The streaming protocol isn't running. You're still live: say 'end the stream' to stop broadcasting."
    assert fake.active[None]


def test_describe_streaming_protocol():
    text = protocols.describe(protocols.find("streaming"))
    assert "check OBS is ready to stream (without going live)" in text and "open Animaze" in text


# ---- review 2026-11-20: never "not live" / "ended" without OBS saying so; the server address is never said --------------

def test_a_platform_obs_cannot_read_is_never_said_as_ended(obs, owner_pc):
    fake = obs(unreadable=("Kick",))
    fake.active.update({None: True, "YouTube": True, "Kick": True, "TikTok": True})
    reply = obsstream.end()
    assert reply == "Stream ended on Twitch, YouTube and TikTok (vertical). I couldn't read whether Kick stopped: check OBS."
    assert ("CallVendorRequest", "stop_output") in fake.calls


def test_status_with_an_unreadable_platform_does_not_say_not_live(obs, owner_pc):
    fake = obs(unreadable=("Kick",))
    assert obsstream.status() == "You're not live on Twitch, YouTube and TikTok (vertical); I couldn't read Kick: check OBS."
    fake.active[None] = True
    assert obsstream.status().endswith("not live on YouTube and TikTok (vertical); I couldn't read Kick.")


def test_the_aitum_list_failing_is_not_ending_only_the_main_platform(obs, owner_pc):
    fake = obs(outputs_error=True)
    fake.active.update({None: True, "YouTube": True})
    reply = obsstream.end()
    assert reply.startswith("I couldn't end the stream: I couldn't read the Aitum Multistream platforms") and "End it in OBS" in reply
    assert fake.active[None], "nothing is called ended that wasn't"
    assert obsstream.still_live() is True


def test_a_slow_answer_is_checked_by_obs_state_not_called_a_failure(obs, owner_pc):
    fake = obs(slow=("StartStream", "start_output"))
    reply = obsstream.go_live()
    assert reply == "You're live on Twitch, YouTube, Kick and TikTok (vertical).", reply
    assert all(fake.active.values())


@pytest.mark.parametrize("server", ["rtmp://live.example.net/app/sk_live_SECRET", "rtmps://x.example:443/live?key=SECRET"])
def test_a_custom_server_address_is_never_said(obs, owner_pc, server):
    obs(service="", server=server)
    for reply in (obsstream.ready_text()[1], obsstream.go_live(), obsstream.status()):
        assert "SECRET" not in reply and "example" not in reply
    assert "your custom server" in obsstream.status()


def test_an_error_after_a_start_still_checks_and_says_what_is_live(obs, owner_pc, monkeypatch):
    fake = obs()
    original = obsstream.Client.vendor

    def boom(self, request, data=None, timeout=None):
        if request == "start_output":
            raise ValueError("bad frame")
        return original(self, request, data, timeout)
    monkeypatch.setattr(obsstream.Client, "vendor", boom)
    reply = obsstream.go_live()
    assert reply.startswith("You're live on Twitch.") and "I stopped partway (bad frame)" in reply
    assert fake.active[None]
