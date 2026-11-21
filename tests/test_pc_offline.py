"""The PC off or asleep: the laptop must know in seconds, not wait minutes on a connection that never answers.

Over Tailscale an offline PC doesn't refuse the connection, it just never replies, so a phone turn sat through the
whole 190 s relay wait, then a minute on the laptop's own CPU model (which never fit Jarvis's prompt in time), before
the cloud tier was even tried.
"""
import socket
import time

import pytest

from core import intelligence, pcbridge


@pytest.fixture
def laptop(monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setenv("JARVIS_PC_TOKEN", "token")
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcbridge, "_down", {"until": 0.0})


def test_an_unreachable_pc_is_known_in_seconds_and_then_skipped(laptop, monkeypatch):
    tries = []

    def never_answers(address, timeout):
        tries.append((address, timeout))
        raise socket.timeout("timed out")
    monkeypatch.setattr(pcbridge.socket, "create_connection", never_answers)
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: pytest.fail("waited on the request"))
    for _ in range(3):
        with pytest.raises(pcbridge.PCError, match="unreachable") as error:
            pcbridge.json_request("/api/pc/intelligence", "POST", {})
        assert error.value.status == 503
    assert tries == [(("pc.example.ts.net", 443), pcbridge._CONNECT_SECONDS)]   # asked once, then remembered
    pcbridge._down["until"] = time.monotonic() - 1                                # a little later it's asked again
    with pytest.raises(pcbridge.PCError):
        pcbridge.json_request("/api/pc/status")
    assert len(tries) == 2


def test_a_reachable_pc_still_gets_the_long_wait_for_its_work(laptop, monkeypatch):
    class Connection:
        def close(self):
            pass

    class Response:
        headers = {"Content-Type": "application/json"}

        def read(self, size):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    waits = []

    class Opener:
        def open(self, req, timeout):
            waits.append(timeout)
            return Response()
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda address, timeout: Connection())
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())
    assert pcbridge.json_request("/api/pc/intelligence", "POST", {}) == {"ok": True}
    assert waits == [190]


def test_with_the_pc_off_the_laptop_goes_to_the_cloud_tier_not_its_cpu(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)

    def off(*args, **kwargs):
        raise pcbridge.PCError("The PC is unreachable.")
    monkeypatch.setattr(pcbridge, "json_request", off)
    monkeypatch.setattr(intelligence, "complete_local", lambda *a, **k: pytest.fail("waited on the laptop's CPU model"))
    ask = [{"role": "user", "content": "What's the capital of France?"}]
    with pytest.raises(intelligence.LocalFailure, match="pc_local_provider_unavailable"):
        intelligence.request("fast", "sys", ask, [])
    monkeypatch.setattr(intelligence, "_cloud_fallback", lambda payload, names, cancelled: "Gemini's answer")
    assert intelligence.request("advanced", "sys", ask, []) == "Gemini's answer"


# ---- cross-device audit F (2026-11-13): a phone request that needs a sleeping PC says so and offers a mission ----------------
def _phone_asks(monkeypatch, said="open Notepad on my PC", error=None):
    from tools import pcview
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")

    def asleep(*a, **k):
        raise error or pcbridge.PCError("The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.",
                                        unreachable=True)
    monkeypatch.setattr(pcbridge, "run", asleep)
    with pcbridge.user_message("owner", said):
        return " ".join(b["text"] for b in pcview.pc_command("open_app", {"app": "notepad"}))


def test_a_sleeping_pc_is_said_plainly_with_an_offer_to_queue_not_a_raw_error(monkeypatch):
    from core import missions
    text = _phone_asks(monkeypatch)
    assert "asleep or unreachable" in text and "Tailscale" not in text
    assert 'queue "open Notepad on my PC" as a mission' in text
    assert missions.listing() == [], "an offer only: nothing is queued until he says yes"


def test_other_pc_errors_keep_their_own_words(monkeypatch):
    text = _phone_asks(monkeypatch, error=pcbridge.PCError("The PC rejected this pairing. Its owner device token needs to be linked again.", 502))
    assert text.startswith("The PC rejected this pairing") and "mission" not in text


def test_the_pc_probe_for_a_fact_does_not_offer_a_mission_and_never_guesses(monkeypatch):
    from core import pcfast
    _phone_asks(monkeypatch, said="what graphics card do I have")
    reply = pcfast.ask_pc("what graphics card do I have")
    assert reply.startswith("I can't reach the PC right now") and "won't guess" in reply and "mission" not in reply


def test_status_and_stop_say_asleep_without_an_offer(monkeypatch):
    from tools import pcview
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")

    def asleep(*a, **k):
        raise pcbridge.PCError("The PC is unreachable.", unreachable=True)
    monkeypatch.setattr(pcbridge, "json_request", asleep)
    for answer in (pcview.pc_status(), pcview.stop_pc_control()):
        assert "asleep or unreachable" in answer and "mission" not in answer


def test_the_real_transport_marks_an_unanswering_pc_as_unreachable(monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "http://192.168.1.20:8765")
    monkeypatch.setenv("JARVIS_PC_TOKEN", "t")
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcbridge, "_answers", lambda url: False)
    with pytest.raises(pcbridge.PCError) as info:
        pcbridge.request("/api/pc/status")
    assert info.value.unreachable is True and info.value.status == 503


def test_a_stop_for_a_sleeping_pc_says_so_and_still_cancels_the_phones_turn(monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from core import appserver, storage
    from tests.test_app import FakeBrain, pair
    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    (storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    brain = FakeBrain("owner")
    brain._lock = threading.Lock()
    brain._lock.acquire()                                                   # the phone's PC task is still running
    client = TestClient(appserver.AppServer(lambda uid: brain).app)
    headers = pair(client)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)

    def asleep(*a, **k):
        raise pcbridge.PCError("The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.", unreachable=True)
    monkeypatch.setattr(pcbridge, "json_request", asleep)
    reply = client.post("/api/chat", json={"text": "stop", "speak": False}, headers=headers).json()["reply"]
    assert reply.startswith("The PC is asleep or unreachable") and "Tailscale" not in reply
