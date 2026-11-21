"""The PC makes the phone's reply audio when it is awake; the laptop makes it when the PC cannot.

Measured 2026-11-19 on the laptop (4 cores held at 1.6 GHz, no graphics card): making the reply's audio takes 1.4 s for
6 words, 4.5 s for 18 and 10.4 s for 48, and the phone gets no sound until all of it is made. The PC that just did the
thinking is 23 ms away and makes the same voice on its graphics card. The laptop stays the fallback: a PC that is off,
slow or older than this route must never cost the phone its voice."""
import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

from core import pcbridge, storage, turntrace, users


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    turntrace._spans.clear()
    monkeypatch.setattr(pcbridge, "_down", {"until": 0.0, "healthy_until": 0.0})
    monkeypatch.setattr(pcbridge, "_voice_down", {"until": 0.0})
    yield
    turntrace._spans.clear()


@pytest.fixture
def laptop(monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setenv("JARVIS_PC_TOKEN", "token")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)


class _Connection:
    def close(self):
        pass


class _Response:
    def __init__(self, data, ctype="audio/mpeg"):
        self.data, self.headers = data, {"Content-Type": ctype}

    def read(self, size=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _pc(monkeypatch, respond, connect=None):
    seen = {"requests": [], "connects": []}

    class Opener:
        def open(self, req, timeout):
            seen["requests"].append((req.full_url, json.loads(req.data), dict(req.header_items()), timeout))
            return respond(req, timeout)

    def connection(address, timeout):
        seen["connects"].append((address, timeout))
        if connect:
            connect()
        return _Connection()
    monkeypatch.setattr(pcbridge.socket, "create_connection", connection)
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())
    return seen


# -- the hop ----------------------------------------------------------------------------------------------------------
def test_the_laptop_asks_the_pc_for_the_audio_over_the_owners_link(laptop, monkeypatch):
    seen = _pc(monkeypatch, lambda req, timeout: _Response(b"\xff\xfbaudio"))
    assert pcbridge.speak("Good morning, sir.") == b"\xff\xfbaudio"
    url, body, headers, timeout = seen["requests"][0]
    assert url == "https://pc.example.ts.net/api/pc/speak" and body == {"text": "Good morning, sir."}
    assert headers["Authorization"] == "Bearer token" and headers["X-jarvis-pc-hop"] == "1"
    assert timeout <= 10                                           # never the minutes a GPU render may take
    assert seen["connects"][0][1] <= 1.0                           # a quick look first: the PC answers in 23 ms


def test_the_pc_gets_less_time_than_the_laptop_would_need(laptop, monkeypatch):
    """Live 2026-11-19: right after a PC restart its voice twice took the whole 8 s wait and then the laptop made it
    anyway (12-13 s replies). A warm PC makes 48 words in 0.32-0.35 s; the laptop needs 1.3 s for 6 words and 8.1-8.5 s
    for 48. So the PC gets a little longer than its slowest warm time, never longer than the laptop would take."""
    seen = _pc(monkeypatch, lambda req, timeout: _Response(b"\xff\xfbaudio"))
    pcbridge.speak("The kettle is on the stove.")
    short = seen["requests"][-1][3]
    pcbridge.speak("The forecast for tomorrow is sunny with a light breeze from the west in the morning. " * 3)
    long = seen["requests"][-1][3]
    pcbridge.speak("word " * 1200)
    longest = seen["requests"][-1][3]
    assert 1.0 <= short <= 2.5
    assert short < long <= 3.5                                     # 48 words: the laptop needs 8 s
    assert longest <= 15


def test_a_pc_whose_jarvis_is_down_behind_the_proxy_is_not_asked_again_for_a_while(laptop, monkeypatch):
    """The PC's Tailscale proxy answers 502 while Jarvis on it is restarting or stopped (live 2026-11-19)."""
    import urllib.error

    def bad_gateway(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
    seen = _pc(monkeypatch, bad_gateway)
    with pytest.raises(pcbridge.PCError):
        pcbridge.speak("Hello.")
    with pytest.raises(pcbridge.PCError):
        pcbridge.speak("Hello.")
    assert len(seen["requests"]) == 1                              # remembered for a while
    assert pcbridge._down["until"] == 0.0                          # the voice hop still never marks the thinking down


def test_a_pc_that_does_not_answer_costs_one_second_and_only_the_voice_remembers(laptop, monkeypatch):
    tries = []

    def never(address, timeout):
        tries.append(timeout)
        raise socket.timeout("timed out")
    monkeypatch.setattr(pcbridge.socket, "create_connection", never)
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: pytest.fail("waited on the request"))
    for _ in range(3):
        with pytest.raises(pcbridge.PCError):
            pcbridge.speak("Hello.")
    assert tries == [pcbridge._VOICE_CONNECT_SECONDS] and tries[0] <= 1.0      # asked once, then remembered
    # ...and the thinking is still tried with its own, longer look: a one-second miss is not a sleeping PC
    assert pcbridge._down["until"] == 0.0


def test_a_pc_known_to_be_asleep_is_not_asked_at_all(laptop, monkeypatch):
    pcbridge._down["until"] = time.monotonic() + 20
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda *a: pytest.fail("asked a sleeping PC"))
    with pytest.raises(pcbridge.PCError) as error:
        pcbridge.speak("Hello.")
    assert error.value.unreachable


def test_a_pc_that_just_answered_is_not_probed_again(laptop, monkeypatch):
    pcbridge._down["healthy_until"] = time.monotonic() + 5         # the thinking came back a moment ago
    seen = _pc(monkeypatch, lambda req, timeout: _Response(b"\xff\xfbaudio"))
    pcbridge.speak("Hello.")
    assert seen["connects"] == []


def test_a_slow_voice_does_not_mark_the_pc_asleep(laptop, monkeypatch):
    def slow(req, timeout):
        raise TimeoutError("timed out")
    _pc(monkeypatch, slow)
    with pytest.raises(pcbridge.PCError) as error:
        pcbridge.speak("Hello.")
    assert not error.value.unreachable
    assert pcbridge._down["until"] == 0.0                          # the next turn's thinking still goes to the PC
    assert pcbridge._voice_down["until"] > time.monotonic()        # only the voice waits a while before asking again
    # ...and only briefly: a PC that was slow once (its graphics card busy) is not as gone as one that refuses
    # (review 2026-11-19: 30 s of 1.3-8.5 s laptop voice for one slow reply)
    assert pcbridge._voice_down["until"] <= time.monotonic() + pcbridge._VOICE_SLOW_SECONDS
    assert pcbridge._VOICE_SLOW_SECONDS < pcbridge._DOWN_SECONDS


def test_an_older_pc_or_a_reply_that_is_not_audio_is_refused(laptop, monkeypatch):
    import urllib.error

    def no_route(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    _pc(monkeypatch, no_route)
    with pytest.raises(pcbridge.PCError) as error:
        pcbridge.speak("Hello.")
    assert error.value.status == 404
    _pc(monkeypatch, lambda req, timeout: _Response(b'{"detail": "no"}', "application/json"))
    with pytest.raises(pcbridge.PCError):
        pcbridge.speak("Hello.")
    _pc(monkeypatch, lambda req, timeout: _Response(b""))
    with pytest.raises(pcbridge.PCError):
        pcbridge.speak("Hello.")


def test_without_a_pc_link_nothing_is_asked(monkeypatch):
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    with pytest.raises(pcbridge.PCError):
        pcbridge.speak("Hello.")


# -- the phone server chooses ---------------------------------------------------------------------------------------------
@pytest.fixture
def server(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    (storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return appserver.AppServer(lambda uid: None)


def _audio(url):
    return (storage.DATA / "cache" / url.rsplit("/", 1)[1]).read_bytes()


def test_the_phones_audio_comes_from_the_pc_when_it_is_awake(laptop, server, monkeypatch):
    monkeypatch.setattr(pcbridge, "speak", lambda text: b"\xff\xfbfrom the pc")
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: pytest.fail("made on the laptop's CPU"))
    since = time.time() - 0.01
    assert _audio(server._speak("Good morning, sir.")) == b"\xff\xfbfrom the pc"
    assert turntrace.breakdown(since)["voice"]["parts"].keys() == {"pc"}


def test_the_laptop_makes_it_when_the_pc_cannot(laptop, server, monkeypatch):
    def off(text):
        raise pcbridge.PCError("The PC is unreachable.", unreachable=True)
    monkeypatch.setattr(pcbridge, "speak", off)
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"\xff\xfbfrom the laptop")
    since = time.time() - 0.01
    assert _audio(server._speak("Good morning, sir.")) == b"\xff\xfbfrom the laptop"
    assert "local" in turntrace.breakdown(since)["voice"]["parts"]


def test_a_pc_attempt_that_failed_leaves_its_time_in_the_ledger(laptop, server, monkeypatch):
    def slow(text):
        time.sleep(0.08)
        raise pcbridge.PCError("The PC did not make the voice in time.")
    monkeypatch.setattr(pcbridge, "speak", slow)
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"\xff\xfblocal")
    since = time.time() - 0.01
    server._speak("Good morning, sir.")
    parts = turntrace.breakdown(since)["voice"]["parts"]
    assert parts.keys() == {"pc failed", "local"} and parts["pc failed"] >= 0.05


def test_the_switch_keeps_the_voice_on_the_laptop(laptop, server, monkeypatch):
    monkeypatch.setenv("JARVIS_PHONE_VOICE", "local")
    monkeypatch.setattr(pcbridge, "speak", lambda text: pytest.fail("asked the PC with the switch on local"))
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"\xff\xfblocal")
    assert _audio(server._speak("Good morning, sir.")) == b"\xff\xfblocal"


def test_the_pc_makes_its_own_companions_audio_itself(server, monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(pcbridge, "speak", lambda text: pytest.fail("the PC asked itself"))
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"\xff\xfblocal")
    assert _audio(server._speak("Good morning, sir.")) == b"\xff\xfblocal"


def test_no_voice_anywhere_is_still_a_reply_without_audio(laptop, server, monkeypatch):
    def off(text):
        raise pcbridge.PCError("The PC is unreachable.", unreachable=True)

    def broken(text):
        raise RuntimeError("no voice model")
    monkeypatch.setattr(pcbridge, "speak", off)
    monkeypatch.setattr("audio.tts.mp3_for", broken)
    assert server._speak("Good morning, sir.") is None


# -- the PC's side ----------------------------------------------------------------------------------------------------
@pytest.fixture
def pc(monkeypatch):
    from core import appserver

    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    made = []

    def mp3(text):
        made.append(text)
        return b"\xff\xfbkokoro"
    monkeypatch.setattr("audio.tts.mp3_voiced", mp3)
    client = TestClient(appserver.AppServer(lambda uid: None).app)
    client.made = made
    return client


def _pair(client, uid="owner"):
    from tools import voice

    voice.pair_codes["222222"] = uid
    r = client.post("/api/pair", json={"code": "222222", "device": "laptop link"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_pc_returns_the_audio_for_the_owners_link(pc):
    r = pc.post("/api/pc/speak", json={"text": "Good morning, sir."}, headers=_pair(pc))
    assert r.status_code == 200 and r.content == b"\xff\xfbkokoro"
    assert r.headers["content-type"] == "audio/mpeg" and pc.made == ["Good morning, sir."]


def test_only_the_owners_link_and_only_real_text(pc):
    assert pc.post("/api/pc/speak", json={"text": "hi"}).status_code == 401
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert pc.post("/api/pc/speak", json={"text": "hi"}, headers=_pair(pc, wife)).status_code == 403
    owner = _pair(pc)
    for bad in ({}, {"text": ""}, {"text": "   "}, {"text": 5}, {"text": "x" * 6001}):
        assert pc.post("/api/pc/speak", json=bad, headers=owner).status_code == 400
    assert pc.made == []


def test_a_voice_that_fails_on_the_pc_is_a_refusal_the_laptop_can_fall_back_from(pc, monkeypatch):
    def broken(text):
        raise RuntimeError("CUDA error")
    monkeypatch.setattr("audio.tts.mp3_voiced", broken)
    r = pc.post("/api/pc/speak", json={"text": "hi"}, headers=_pair(pc))
    assert r.status_code == 503 and "CUDA" not in r.text


def test_the_laptop_does_not_serve_the_route_as_if_it_had_a_graphics_card(monkeypatch):
    from core import appserver

    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr("audio.tts.mp3_voiced", lambda text: pytest.fail("made on the laptop for a relay"))
    client = TestClient(appserver.AppServer(lambda uid: None).app)
    assert client.post("/api/pc/speak", json={"text": "hi"}, headers=_pair(client)).status_code == 503


# -- review round 1: silence is not a voice ---------------------------------------------------------------------------
class _NoVoice:
    """A Speaker whose every voice failed: _synthesize gives nothing back."""
    def __init__(self, *a, **k):
        pass

    def _synthesize(self, text):
        return None


def test_a_reply_no_voice_could_say_is_an_error_for_callers_that_can_fall_back(monkeypatch):
    from audio import tts
    monkeypatch.setattr(tts, "Speaker", _NoVoice)
    with pytest.raises(RuntimeError, match="nothing was voiced"):
        tts.mp3_voiced("Good morning, sir.")
    assert tts.mp3_for("Good morning, sir.")                # the old call still returns its short silence


def test_the_pc_refuses_rather_than_send_silence(monkeypatch):
    from audio import tts
    from core import appserver
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(tts, "Speaker", _NoVoice)
    client = TestClient(appserver.AppServer(lambda uid: None).app)
    assert client.post("/api/pc/speak", json={"text": "Good morning, sir."}, headers=_pair(client)).status_code == 503


def test_a_muted_voice_never_falls_back_to_speaking_out_loud(monkeypatch):
    """The MP3 is for a phone somewhere else: the last resort (Windows' own voice, out of this machine's speakers) is
    never used for it. Found in review: a phone reply could have been read aloud in the room where the PC is."""
    from audio import tts
    aloud = []
    monkeypatch.setattr(tts.Speaker, "_offline_say", staticmethod(lambda text: aloud.append(text)))
    monkeypatch.setattr(tts.Speaker, "_say_with", lambda self, text, voice, rate=None: (_ for _ in ()).throw(RuntimeError("no voice")))
    monkeypatch.setattr(tts, "kokoro_say", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no kokoro")))
    monkeypatch.delenv("JARVIS_LOCAL_ONLY", raising=False)
    quiet = tts.Speaker("en-GB-RyanNeural", muted=True)
    assert quiet._synthesize("Good morning, sir.") is None and aloud == []
