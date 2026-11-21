"""'What's the hold up?' gets a progress answer without stopping the task."""
import threading
import time

import pytest

from core import progress


@pytest.mark.parametrize("text", ["What's the hold up?", "jarvis what's taking so long", "how's it going",
                                  "are you done yet", "[Typed into their Jarvis phone app.] status",
                                  "sa k pase", "ou fini?", "où en es-tu ?", "ça avance ?"])
def test_status_questions(text):
    assert progress.is_status_question(text)


@pytest.mark.parametrize("text", ["stop", "never mind", "uninstall discord", "open spotify",
                                  "what's the weather like tomorrow in Miami for the whole family trip this weekend please"])
def test_not_status_questions(text):
    assert not progress.is_status_question(text)


def test_report():
    progress.done()
    assert "nearly there" in progress.report()
    progress.start("uninstalling Proton VPN")
    progress.step("waiting for Proton VPN's uninstaller to finish")
    out = progress.report()
    assert out.startswith("I'm uninstalling Proton VPN") and "uninstaller" in out and "Still going" in out
    assert progress.report("ht").startswith("M ap")
    progress.done()


def test_phone_status_question_does_not_wait_for_the_task(client_factory=None):
    from fastapi.testclient import TestClient

    from core import appserver, users
    from tools import voice

    release = threading.Event()

    class SlowBrain:
        def __init__(self, uid):
            self._lock = threading.Lock()

        def ask(self, content, on_sentence):
            with self._lock:
                progress.start("uninstalling Proton VPN")
                release.wait(5)
                progress.done()
                on_sentence("Done.")

    server = appserver.AppServer(lambda uid: SlowBrain(uid))
    server._speak = lambda text: None
    c = TestClient(server.app)
    voice.pair_codes["222222"] = "owner"
    h = {"Authorization": "Bearer " + c.post("/api/pair", json={"code": "222222"}).json()["token"]}
    worker = threading.Thread(target=lambda: c.post("/api/chat", headers=h, json={"text": "uninstall proton vpn", "speak": False}))
    worker.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    r = c.post("/api/chat", headers=h, json={"text": "what's the hold up?", "speak": False}).json()
    assert time.monotonic() - t0 < 2 and r["reply"].startswith("I'm uninstalling Proton VPN")
    release.set()
    worker.join(5)
