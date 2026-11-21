"""Offline tests for the phone app server (fake brains, no microphone)."""
import pytest
from fastapi.testclient import TestClient

from core import users


class FakeBrain:
    def __init__(self, uid):
        self.uid = uid

    def ask(self, content, on_sentence):
        text = content[-1]["text"]
        on_sentence(f"Hello {users.title(self.uid)}.")
        if "picture" in text:
            from core import hud, storage

            img = storage.user_dir(self.uid) / "fashion" / "tryons" / "t.jpg"
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(b"\xff\xd8jpeg")
            hud.media(img, "Try-on")


@pytest.fixture
def client(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"ID3fake")
    server = appserver.AppServer(lambda uid: FakeBrain(uid))
    appserver.hud.add_media_listener(server._collect_media)
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(server.app)


def pair(client, uid="owner"):
    from tools import voice

    voice.pair_codes["111111"] = uid
    r = client.post("/api/pair", json={"code": "111111", "device": "iPhone"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_pairing(client):
    assert client.post("/api/pair", json={"code": "000000"}).status_code == 403
    headers = pair(client)
    me = client.get("/api/me", headers=headers).json()
    assert me["title"] == "sir" and me["vapid"]
    assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    # the code works once
    assert client.post("/api/pair", json={"code": "111111"}).status_code == 403


def test_chat_is_answered_as_the_paired_person(client):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    r = client.post("/api/chat", json={"text": "hi", "speak": True}, headers=pair(client, wife)).json()
    assert r["reply"] == "Hello madame." and r["audio"].startswith("/api/audio/")
    assert client.get(r["audio"]).content == b"ID3fake"


def test_images_come_back_and_stay_private(client):
    owner = pair(client)
    r = client.post("/api/chat", json={"text": "show me a picture", "speak": False}, headers=owner).json()
    url = r["images"][0]["url"]
    assert client.get(url, headers=owner).content == b"\xff\xd8jpeg"
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert client.get(url, headers=pair(client, wife)).status_code == 404  # her folder, not his
    assert client.get("/api/file/u/../../users.json", headers=owner).status_code == 404


def test_push_subscription_saved(client):
    headers = pair(client)
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post("/api/push", json={"subscription": sub}, headers=headers).json()["ok"]
    assert users.get("owner")["devices"][0]["push"] == sub


def test_push_goes_to_the_right_person(monkeypatch):
    import pywebpush

    from core import appserver

    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda subscription_info, **k: sent.append(subscription_info["endpoint"]))
    # This test is about routing to the right person; quiet hours (22:00-07:00 by default) are covered in
    # test_notify, so don't let the wall clock decide whether the push goes out.
    from core import notify
    monkeypatch.setattr(notify, "allowed", lambda uid, category, urgent=False: True)
    appserver.vapid_public_key()
    wife = users.add("Marie", {"en": "madame"}, "wife")
    users.update("owner", devices=[{"id": "a", "token_hash": "h1", "push": {"endpoint": "his"}}])
    users.update(wife, devices=[{"id": "b", "token_hash": "h2", "push": {"endpoint": "hers"}}])
    appserver.push("Reminder", "dentist", wife)
    assert sent == ["hers"]
    sent.clear()
    appserver.push("Household", "bins", None)
    assert sorted(sent) == ["hers", "his"]


def test_app_shell_is_served(client):
    assert "J.A.R.V.I.S." in client.get("/").text
    assert client.get("/manifest.webmanifest").json()["short_name"] == "Jarvis"
    assert client.get("/sw.js").status_code == 200


def test_household_file_endpoint_enforces_teaching_scope(client):
    from core import teaching
    from tools import engineering
    owner = pair(client)
    lesson = teaching.submit({"title": "Private lesson", "topic": "Python", "source": "owner",
        "license": "mine", "version": "1", "content": "Owner-only source material"}, owner=True)
    doc = f"/api/file/h/engineering/docs/{lesson['doc_id']}.json"
    assert client.get(doc, headers=owner).status_code == 200
    member = users.add("Library member", {})
    member_auth = pair(client, member)
    for url in (doc, "/api/file/h/engineering/index.json", "/api/file/h/teaching/gateway.sqlite3"):
        assert client.get(url, headers=member_auth).status_code == 403
    shared = engineering._save_doc("Shared reference", "shared text", "owner", "shared")
    assert client.get(f"/api/file/h/engineering/docs/{shared['id']}.json", headers=member_auth).status_code == 200


def test_command_line_pairing_code(client):
    import manage

    manage.main(["add-person", "Marie", "wife", "madame", "madanm", "madame"])
    manage.main(["pair", "Marie"])
    code = next(iter(manage.file_codes()))
    r = client.post("/api/pair", json={"code": code, "device": "iPhone"})
    assert r.status_code == 200 and r.json()["title"] == "madame"
    assert client.post("/api/pair", json={"code": code}).status_code == 403  # works once


def test_dashboard_shows_homelab_problems(client, monkeypatch):
    from core import appserver, storage

    monkeypatch.setattr(appserver.AppServer, "_homelab", staticmethod(lambda: {"error": "offline"}))
    monkeypatch.setattr(appserver.AppServer, "_playing", staticmethod(lambda: []), raising=False)
    storage.save_global("homelab_watch.json", {"problems": {"mount:jellyfin": "jellyfin lost its connection"}})
    d = client.get("/api/dashboard", headers=pair(client)).json()
    assert d["problems"] == ["jellyfin lost its connection"]


def test_file_route_stays_inside_the_right_folder(client):
    from core import storage

    h = pair(client)
    (storage.DATA / "users" / "owner").mkdir(parents=True, exist_ok=True)
    (storage.DATA / "users" / "owner" / "ok.txt").write_text("mine")
    sibling = storage.DATA / "users" / "owner2"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "secret.txt").write_text("not yours")
    assert client.get("/api/file/u/ok.txt", headers=h).text == "mine"
    for bad in ["/api/file/u/../owner2/secret.txt", "/api/file/u/..%2Fowner2%2Fsecret.txt",
                "/api/file/x/ok.txt", "/api/file/h/../users/owner2/secret.txt"]:
        assert client.get(bad, headers=h).status_code == 404, bad


def test_pairing_locks_after_repeated_wrong_codes(client):
    codes = [client.post("/api/pair", json={"code": "000000"}).status_code for _ in range(6)]
    assert codes[:5] == [403] * 5 and codes[5] == 429


def test_upload_limits(client, monkeypatch):
    from core import appserver

    h = pair(client)
    monkeypatch.setattr(appserver, "MAX_UPLOAD", 1000)
    assert client.post("/api/chat", headers=h, json={"text": "x" * 9000}).status_code == 413
    assert client.post("/api/chat", headers=h, json={"text": "hi", "image": "data:image/png;base64," + "A" * 5000}).status_code == 413


# -- one Brain per person per machine (plan upgrade 10) ------------------------------------------------------------------
def real_brain(monkeypatch, pc=None, request=None):
    """The real Brain (quick answers off, the PC layer answering with `pc(text)`, the model faked), as the voice makes it."""
    from types import SimpleNamespace

    from anthropic.types import TextBlock

    from brain import llm
    from core import intelligence, pcfast, quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    calls = []

    def handle(text, history=None, say=None, earcon=None):
        calls.append({"text": text, "say": say, "earcon": earcon})
        return pc(text) if pc else None
    monkeypatch.setattr(pcfast, "handle", handle)
    monkeypatch.setattr(intelligence, "request", request or (lambda role, system, history, tools, cancelled, on_text=None, **kw:
                        SimpleNamespace(content=[TextBlock(type="text", text="Certainly, sir.")], stop_reason="end_turn")))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.context = lambda: "It is Tuesday."
    b.earcon = lambda: None            # the voice's cue for a PC request (main.brain_for sets it)
    return b, calls


def voice_server(monkeypatch, b):
    """An AppServer handed the voice's own brains, as main.py does on the PC."""
    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    brains = {"owner": b}
    server = appserver.AppServer(lambda uid: brains[uid])
    return server, TestClient(server.app)


def text_of(message):
    content = message["content"]
    return content if isinstance(content, str) else " ".join(p.get("text", "") for p in content if isinstance(p, dict))


def test_typed_on_the_pc_and_spoken_are_one_conversation(monkeypatch):
    """The companion's typed 'open paint' and then a spoken 'and close it': the same Brain, the same history."""
    b, calls = real_brain(monkeypatch, pc=lambda text: "Opened Paint." if "open paint" in text else
                          ("Closed Paint." if "close it" in text else None))
    server, client = voice_server(monkeypatch, b)
    r = client.post("/api/chat", json={"text": "open paint", "speak": False}, headers=pair(client)).json()
    assert r["reply"] == "Opened Paint." and server._brain("owner") is b
    heard = []
    b.ask("and close it", heard.append)
    assert heard == ["Closed Paint."]
    assert [m["role"] for m in b.messages] == ["user", "assistant", "user", "assistant"]
    assert "open paint" in text_of(b.messages[0]) and text_of(b.messages[2]) == "and close it"
    # the typed turn was gathered (no "Right away, sir", no PC cue); the voice's own turn still gets both
    assert calls[0]["say"] is None and calls[0]["earcon"] is None
    assert callable(calls[1]["say"]) and calls[1]["earcon"] is b.earcon
    assert b.live is True, "a typed turn never makes the voice's brain gathered for good"


def test_one_lock_a_typed_turn_waits_for_the_spoken_one(monkeypatch):
    import json
    import threading
    import time
    from types import SimpleNamespace

    from anthropic.types import TextBlock
    started, release, order = threading.Event(), threading.Event(), []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        asked = "spoken" if "spoken question" in json.dumps(history[-1]) else "typed"
        order.append(("start", asked))
        if asked == "spoken":
            started.set()
            release.wait(5)
        order.append(("end", asked))
        return SimpleNamespace(content=[TextBlock(type="text", text=f"Answer to the {asked} one, sir.")],
                               stop_reason="end_turn")
    b, _ = real_brain(monkeypatch, request=request)
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    spoken = threading.Thread(target=b.ask, args=("a spoken question about the weekend", [].append))
    spoken.start()
    assert started.wait(5)
    got = {}
    typed = threading.Thread(target=lambda: got.update(client.post(
        "/api/chat", json={"text": "a typed question about dinner", "speak": False}, headers=headers).json()))
    typed.start()
    time.sleep(0.4)
    assert order == [("start", "spoken")], "the typed turn waits on the same lock"
    release.set()
    spoken.join(5)
    typed.join(5)
    assert order == [("start", "spoken"), ("end", "spoken"), ("start", "typed"), ("end", "typed")]
    assert got["reply"] == "Answer to the typed one, sir."
    roles = [m["role"] for m in b.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert "spoken question" in text_of(b.messages[0]) and "typed question" in text_of(b.messages[2])


def test_main_hands_the_voice_brains_to_the_app_server():
    """main.py on the PC: AppServer gets brain_for itself, never a second Brain per person."""
    import ast
    from pathlib import Path

    import main
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "AppServer"]
    assert len(calls) == 1 and isinstance(calls[0].args[0], ast.Name) and calls[0].args[0].id == "brain_for"


# -- review 2026-11-11: one Brain, two channels. A cut-in belongs to the channel it was said on. -----------------------
def _words(message):
    """A message's text, whether stored as a string, dict blocks or the model's TextBlock objects."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return " ".join((p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "") or "") for p in content)


def _held_request(release, started, slow="typed question", replies=None):
    """A fake model: the turn whose newest message names `slow` waits in the model until `release` is set."""
    import json
    from types import SimpleNamespace

    from anthropic.types import TextBlock
    replies = replies or {}

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        newest = json.dumps(history[-1])
        if slow in newest:
            started.set()
            release.wait(5)
        text = next((reply for key, reply in replies.items() if key in newest), "Certainly, sir.")
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")
    return request


def test_a_spoken_stop_never_cuts_the_typed_turn_and_the_spoken_command_it_meant_never_runs(monkeypatch):
    """The companion's typed turn holds the Brain; he says a PC command aloud (its turn waits), hears nothing, and says
    'stop' (main.respond: cancel on the voice's Brain). The typed turn is answered in full; the spoken command never
    acts or speaks, and history keeps that he asked and cut in."""
    import threading
    import time
    from brain.llm import INTERRUPTED_BEFORE
    started, release = threading.Event(), threading.Event()
    acted = []
    b, _ = real_brain(monkeypatch, request=_held_request(release, started, replies={"typed question": "Dinner is at seven, sir."}),
                      pc=lambda text: (acted.append(text) or "Closed the browser.") if "close the browser" in text else None)
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    got = {}
    typed = threading.Thread(target=lambda: got.update(client.post(
        "/api/chat", json={"text": "a typed question about dinner", "speak": False}, headers=headers).json()))
    typed.start()
    assert started.wait(5)
    spoken: list[str] = []
    voice = threading.Thread(target=b.ask, args=("close the browser", spoken.append))   # main.respond's worker
    voice.start()
    time.sleep(0.3)
    assert voice.is_alive(), "the spoken turn waits for the typed one"
    b.cancel("", "")                                  # his cut-in, from the voice loop's thread
    release.set()
    typed.join(5)
    voice.join(5)
    assert got["reply"] == "Dinner is at seven, sir.", "the typed turn was not the one he cut"
    assert acted == [] and spoken == [], "the spoken command he cut off still ran"
    assert [m["role"] for m in b.messages] == ["user", "assistant", "user", "assistant"]
    assert text_of(b.messages[2]) == "close the browser" and b.messages[3]["content"] == INTERRUPTED_BEFORE


def test_a_spoken_turn_after_the_cut_one_runs_normally(monkeypatch):
    """The cut only drops the turn that waited through it: his next spoken command is answered."""
    import threading
    import time
    started, release = threading.Event(), threading.Event()
    b, _ = real_brain(monkeypatch, request=_held_request(release, started),
                      pc=lambda text: "Closed the browser." if "close the browser" in text else None)
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    typed = threading.Thread(target=lambda: client.post(
        "/api/chat", json={"text": "a typed question about dinner", "speak": False}, headers=headers))
    typed.start()
    assert started.wait(5)
    voice = threading.Thread(target=b.ask, args=("open the music player", [].append))
    voice.start()
    time.sleep(0.3)
    b.cancel("", "")
    release.set()
    typed.join(5)
    voice.join(5)
    heard: list[str] = []
    b.ask("close the browser", heard.append)
    assert heard == ["Closed the browser."]


def test_a_cut_into_a_spoken_reply_still_playing_after_a_typed_turn_trims_the_spoken_reply(monkeypatch):
    """The spoken reply is still playing when a typed turn is answered; he then cuts into the SPOKEN reply. History
    keeps what he heard of it, the typed reply (shown in full) stays, and 'go on' has the unheard rest."""
    import threading
    b, _ = real_brain(monkeypatch, request=_held_request(threading.Event(), threading.Event(), slow="(nothing held)",
                      replies={"typed question": "Dinner is at seven, sir.",
                               "weekend": "Saturday is sunny and warm. Sunday brings heavy rain all day."}))
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    b.ask("how is the weekend looking", [].append)         # spoken: ask() returned, the voice still plays it
    r = client.post("/api/chat", json={"text": "a typed question about dinner", "speak": False}, headers=headers).json()
    assert r["reply"] == "Dinner is at seven, sir."
    b.cancel("Saturday is sunny and warm.", "Sunday brings heavy rain all day.")
    assistant = [_words(m) for m in b.messages if m["role"] == "assistant"]
    assert assistant[0] == "Saturday is sunny and warm. [the user cut me off about here and did not hear the rest]"
    assert assistant[-1] == "Dinner is at seven, sir."
    assert b._unsaid and b._unsaid[0] == "Sunday brings heavy rain all day."


def test_a_cut_into_a_spoken_reply_while_a_typed_turn_runs_waits_for_it_and_leaves_it_whole(monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    b, _ = real_brain(monkeypatch, request=_held_request(release, started, replies={
        "typed question": "Your typed answer, sir.",
        "weekend": "Saturday is sunny and warm. Sunday brings heavy rain all day."}))
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    b.ask("how is the weekend looking", [].append)
    got = {}
    typed = threading.Thread(target=lambda: got.update(client.post(
        "/api/chat", json={"text": "a typed question about paris", "speak": False}, headers=headers).json()))
    typed.start()
    assert started.wait(5)
    b.cancel("Saturday is sunny and warm.", "Sunday brings heavy rain all day.")   # into the SPOKEN reply
    release.set()
    typed.join(5)
    assert got["reply"] == "Your typed answer, sir."
    assistant = [_words(m) for m in b.messages if m["role"] == "assistant"]
    assert assistant == ["Saturday is sunny and warm. [the user cut me off about here and did not hear the rest]",
                         "Your typed answer, sir."]


def test_explaining_a_skill_in_the_companion_leaves_the_spoken_reply_cuttable(monkeypatch):
    """/api/skills/explain runs on the voice's own Brain: it must not disarm the cut-in of a spoken reply still playing,
    and a 'stop' said to the voice earlier never silences it."""
    import threading
    b, _ = real_brain(monkeypatch, request=_held_request(threading.Event(), threading.Event(), slow="(nothing held)",
                      replies={"weekend": "Saturday is sunny and warm. Sunday brings heavy rain all day.",
                               "skill-result": "Your disk is forty percent free, sir."}))
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"ID3fake")
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    b.ask("how is the weekend looking", [].append)
    b._cancel.set()                                     # a stop said to the voice, still set
    r = client.post("/api/skills/explain", json={"title": "disk", "text": "C: 40% free"}, headers=headers).json()
    assert r["reply"] == "Your disk is forty percent free, sir."
    b._cancel.clear()
    b.cancel("Saturday is sunny and warm.", "Sunday brings heavy rain all day.")
    assert _words(b.messages[-1]).startswith("Saturday is sunny and warm. [the user cut me off")
    assert b._unsaid and b._unsaid[0] == "Sunday brings heavy rain all day."


def test_a_spoken_cut_in_reaches_a_translation_after_a_typed_turn(monkeypatch):
    """Interpreter mode translates on the voice's Brain: after a typed turn, his cut-in still stops the translation."""
    import threading
    from types import SimpleNamespace

    from anthropic.types import TextBlock
    from core import intelligence
    b, _ = real_brain(monkeypatch)
    _, client = voice_server(monkeypatch, b)
    client.post("/api/chat", json={"text": "a typed question", "speak": False}, headers=pair(client))
    seen = []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        cut = threading.Thread(target=lambda: b.cancel("", ""))    # the voice loop's thread
        cut.start()
        cut.join(5)
        seen.append(cancelled())
        return SimpleNamespace(content=[TextBlock(type="text", text="Bonjou.")], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    b.translate("hello", "English", "Haitian Creole", [].append)
    assert seen == [True]


def test_the_phone_stop_on_the_laptop_still_cuts_the_phone_turn(monkeypatch):
    """On the laptop the app server's stop reaches the PC and cancels the phone's own (gathered) turn."""
    import threading
    from core import pcbridge
    started, release = threading.Event(), threading.Event()
    b, _ = real_brain(monkeypatch, request=_held_request(release, started, slow="old harbor town"))
    _, client = voice_server(monkeypatch, b)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "json_request", lambda *a, **k: {"detail": "Stop requested for the PC task."})
    headers = pair(client)
    got = {}
    asked = threading.Thread(target=lambda: got.update(client.post(
        "/api/chat", json={"text": "tell me a long story about the old harbor town", "speak": False}, headers=headers).json()))
    asked.start()
    assert started.wait(5)
    stop = client.post("/api/chat", json={"text": "stop", "speak": False}, headers=headers).json()
    assert stop["reply"] == "Stop requested for the PC task."
    assert b._cancel.is_set(), "the phone's stop reached the phone's running turn"
    release.set()
    asked.join(5)


def test_wrong_codes_from_one_address_do_not_lock_out_another(client):
    """Cross-device audit 7: the limiter was global, so anyone's wrong guesses paused the owner's own pairing."""
    from starlette.testclient import TestClient
    from tools import voice

    app = client.app
    guesser = TestClient(app, client=("100.64.0.9", 50000))
    owner = TestClient(app, client=("100.64.0.2", 50000))
    assert [guesser.post("/api/pair", json={"code": "000000"}).status_code for _ in range(6)] == [403] * 5 + [429]
    voice.pair_codes["333333"] = "owner"
    assert owner.post("/api/pair", json={"code": "333333", "device": "iPhone"}).status_code == 200
    assert guesser.post("/api/pair", json={"code": "333333"}).status_code == 429, "the guesser stays paused"


def test_guesses_from_many_addresses_still_hit_a_ceiling(client):
    from starlette.testclient import TestClient

    codes = []
    for n in range(20):
        c = TestClient(client.app, client=(f"100.64.1.{n}", 50000))
        codes.append(c.post("/api/pair", json={"code": "000000"}).status_code)
    assert 429 in codes and codes.index(429) == 15
