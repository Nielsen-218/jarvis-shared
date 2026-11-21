"""One thread across machines (plan upgrade 10): each machine appends the finished exchanges to its own per-person ledger
(data/users/<uid>/thread-<machine>.jsonl, shared by Syncthing), and a new conversation's <background> shows what was said
on the OTHER machine in the last two hours. Synthetic exchanges only; no model, no network."""
import json
import time
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from brain import llm
from core import awareness, intelligence, pcbridge, pcfast, quick, storage, thread, users
from tools import memory

NOTE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "


@pytest.fixture
def on(monkeypatch):
    """on('pc') / on('server'): which machine this process is."""
    def switch(name: str) -> None:
        monkeypatch.setattr(pcbridge, "local_pc", lambda: name == "pc")
    return switch


def test_an_exchange_is_appended_to_this_machines_own_file_and_read_back_on_the_other(on):
    on("server")
    thread.append("owner", NOTE + "I'm weighing up a new graphics card.", "A good time to buy, sir.", "phone")
    path = storage.user_dir("owner") / "thread-server.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["said"] == "I'm weighing up a new graphics card.", "the phone app's own note is not his words"
    assert rows[-1]["reply"] == "A good time to buy, sir." and rows[-1]["channel"] == "phone"
    assert b"\r\n" not in path.read_bytes(), "the same bytes on both machines"
    assert not (storage.user_dir("owner") / "thread-pc.jsonl").exists(), "one writer per file"
    assert thread.recent("owner") == [], "the laptop shows the PC's exchanges, never its own"
    on("pc")
    assert [r["said"] for r in thread.recent("owner")] == ["I'm weighing up a new graphics card."]


def test_only_the_last_two_hours_and_six_exchanges_and_each_side_is_clipped(on):
    on("server")
    now = time.time()
    thread.append("owner", "something from this morning", "Noted.", "phone", now=now - 3 * 3600)
    for i in range(8):
        thread.append("owner", f"question number {i}", "x" * 500, "phone", now=now - 600 + i)
    stored = (storage.user_dir("owner") / "thread-server.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(json.loads(stored[-1])["reply"]) == thread.KEEP_CHARS
    on("pc")
    rows = thread.recent("owner", now=now)
    assert [r["said"] for r in rows] == [f"question number {i}" for i in range(2, 8)]
    text = thread.background("owner", now=now)
    assert text.startswith("Recently on their phone") and "this morning" not in text
    assert "x" * thread.SHOW_CHARS not in text and "x" * (thread.SHOW_CHARS - 1) + "…" in text


def test_secrets_are_never_written_and_a_torn_line_is_skipped(on):
    on("server")
    thread.append("owner", "my wifi password is hunter2hunter2", "I won't keep that.", "phone")
    path = storage.user_dir("owner") / "thread-server.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"ts": 17943')                      # caught mid-sync
    assert "hunter2" not in path.read_text(encoding="utf-8")
    on("pc")
    rows = thread.recent("owner")
    assert len(rows) == 1 and rows[0]["said"].startswith("[not kept") and rows[0]["reply"] == "I won't keep that."


def test_the_background_shows_the_other_machine_and_never_another_household_member(on):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    on("server")
    thread.append("owner", "I'm thinking about a new graphics card", "Which budget, sir?", "phone")
    thread.append(wife, "remind me about the dentist on friday", "Done, madame.", "phone")
    on("pc")
    with users.acting_as("owner"):
        mine = memory.context_block(core_only=True)
        full = memory.context_block(core_only=False)
    with users.acting_as(wife):
        hers = memory.context_block(core_only=True)
    for ctx in (mine, full):
        assert "Recently on their phone" in ctx and "new graphics card" in ctx and "Which budget, sir?" in ctx
        assert "dentist" not in ctx
    assert "dentist" in hers and "graphics card" not in hers
    on("server")
    with users.acting_as("owner"):
        assert "Recently at their PC" not in memory.context_block(core_only=True), "nothing said at the PC yet"


def test_the_core_budget_is_not_spent_on_the_other_machine(on, monkeypatch):
    """The thread lines come after the core (tools.memory._core keeps CORE_CHARS for identity, journals and lessons)."""
    on("server")
    for i in range(6):
        thread.append("owner", f"a long phone message {i} " + "words " * 40, "reply " * 40, "phone")
    on("pc")
    with users.acting_as("owner"):
        ctx = memory.context_block(core_only=True)
    core, _, tail = ctx.partition("\nRecently on their phone")
    assert tail and len(core) <= memory.CORE_CHARS
    assert len(tail) < 6 * (2 * thread.SHOW_CHARS + 60) + 300


def test_forgetting_the_last_minutes_reaches_the_thread_too(on):
    on("pc")
    now = time.time()
    thread.append("owner", "an old line", "Old reply.", "voice", now=now - 3600)
    thread.append("owner", "something private just now", "Understood.", "voice", now=now - 60)
    with users.acting_as("owner"):
        awareness.delete(minutes=10)
    rows = [json.loads(x) for x in (storage.user_dir("owner") / "thread-pc.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["said"] for r in rows] == ["an old line"]
    with users.acting_as("owner"):
        awareness.delete(everything=True)
    assert (storage.user_dir("owner") / "thread-pc.jsonl").read_text(encoding="utf-8") == ""


def test_rows_past_a_day_are_dropped_when_the_file_is_next_written(on):
    on("pc")
    now = time.time()
    thread.append("owner", "two days ago", "Old.", "voice", now=now - 2 * 86400)
    thread.append("owner", "today", "New.", "voice", now=now)
    rows = [json.loads(x) for x in (storage.user_dir("owner") / "thread-pc.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["said"] for r in rows] == ["today"]


# -- the Brain writes each finished exchange ------------------------------------------------------------------------
def brain(monkeypatch, pc=None, reply="It's a fine evening, sir."):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: pc(text) if pc else None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(intelligence, "request", lambda role, system, history, tools, cancelled, on_text=None, **kw:
                        SimpleNamespace(content=[TextBlock(type="text", text=reply)], stop_reason="end_turn"))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.context = lambda: "It is Tuesday."
    return b


def rows(name="pc"):
    path = storage.user_dir("owner") / f"thread-{name}.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_voice_turn_and_a_typed_turn_are_both_in_the_thread_with_their_channel(monkeypatch, on):
    on("pc")
    b = brain(monkeypatch, pc=lambda text: "Opened Paint." if "paint" in text else None)
    b.ask("how is the evening looking", [].append)
    with b.gathered("typed"):
        b.ask([{"type": "text", "text": NOTE + "open paint"}], [].append)
    assert [(r["channel"], r["said"], r["reply"]) for r in rows()] == [
        ("voice", "how is the evening looking", "It's a fine evening, sir."), ("typed", "open paint", "Opened Paint.")]


def test_overheard_speech_jarvis_ignored_is_no_exchange_and_a_cut_reply_is_kept_as_cut(monkeypatch, on):
    on("pc")
    b = brain(monkeypatch, reply="[ignore]")
    b.ask("the kettle is boiling again", [].append)
    assert rows() == []
    b = brain(monkeypatch, pc=lambda text: b.cancel("") or "Stopped.")
    b.ask("open notepad and type a long letter", [].append)
    assert rows()[-1]["cut"] is True and rows()[-1]["reply"] == ""


def test_a_failing_ledger_never_costs_the_turn(monkeypatch, on):
    on("pc")
    monkeypatch.setattr(thread, "append", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    heard = []
    brain(monkeypatch).ask("how is the evening looking", heard.append)
    assert heard == ["It's a fine evening, sir."]


def test_the_morning_briefing_prompt_is_never_recorded_as_his_words(monkeypatch, on):
    on("pc")
    b = brain(monkeypatch, reply="Good morning, sir. Two meetings today.")
    b.ask("[Automatic morning briefing: the user just sat down at the PC. Greet them.]", [].append)
    assert rows()[-1]["said"] == "" and rows()[-1]["reply"] == "Good morning, sir. Two meetings today."
    on("server")
    text = thread.background("owner")
    assert "Automatic" not in text and 'Jarvis: "Good morning, sir. Two meetings today." (unprompted)' in text


# -- review 2026-11-11 ---------------------------------------------------------------------------------------------------
def replying(monkeypatch, replies: dict, default="Certainly, sir."):
    """The faked model answers by what the newest message says (a repeated reply would be dropped as a copied one)."""
    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        newest = json.dumps(history[-1])
        text = next((reply for key, reply in replies.items() if key in newest), default)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)


def app_server(monkeypatch, make_brain):
    from fastapi.testclient import TestClient
    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    (storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    server = appserver.AppServer(make_brain)
    return server, TestClient(server.app)


def paired(client, device="iPhone", code="222222"):
    from tools import voice
    voice.pair_codes[code] = "owner"
    r = client.post("/api/pair", json={"code": code, "device": device})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_every_app_channel_note_is_left_out_of_his_words(on, monkeypatch):
    """Voice notes and live mode, the camera, Alexa and Telegram put their own note before his words: instructions,
    never what he said (the camera's used up the shown part, so his question was lost)."""
    on("server")
    b = brain(monkeypatch, reply="It'll be sunny tomorrow, sir.")
    server, _ = app_server(monkeypatch, lambda uid: b)
    notes = ["[Spoken into their Jarvis phone app.] ",
             "[Live camera from their Jarvis phone app — 1 frame(s) of a photo. Describe or answer about what you see. "
             "Keep replies conversational; no markdown.] ",
             "[Spoken to Jarvis through an Alexa Echo. Reply in one or two short spoken sentences, no markdown.] ",
             "[Sent from their phone via Telegram. Reply in plain text, no markdown; they may be away from the PC.] "]
    for i, note in enumerate(notes):
        server._answer("owner", [{"type": "text", "text": note + f"what is the weather like on day {i}"}],
                       f"what is the weather like on day {i}", False)
    assert [r["said"] for r in rows("server")] == [f"what is the weather like on day {i}" for i in range(4)]
    on("pc")
    text = thread.background("owner")
    assert "[" not in text.split("\n", 1)[1] and "Describe or answer" not in text and "Alexa" not in text


def test_a_card_number_spoken_in_groups_is_never_kept(on):
    on("server")
    thread.append("owner", "my card is 4111 1111 1111 1111 exp 04/29", "Noted.", "phone")
    thread.append("owner", "card 4111-1111-1111-1111", "Noted.", "phone")
    thread.append("owner", "call me at 757 555 0134 tomorrow", "Will do.", "phone")
    said = [r["said"] for r in rows("server")]
    assert said[0].startswith("[not kept") and said[1].startswith("[not kept")
    assert said[2] == "call me at 757 555 0134 tomorrow", "a phone number is not a card number"
    assert "4111" not in (storage.user_dir("owner") / "thread-server.jsonl").read_text(encoding="utf-8")


def test_a_phone_turn_stopped_mid_task_keeps_the_report_it_delivered(on, monkeypatch):
    """On the phone a stopped PC turn's report is still delivered: the thread keeps it (marked cut), never a blank."""
    on("server")
    report = "Stopped. I had closed two of your five browser windows."
    holder = {}
    b = brain(monkeypatch, pc=lambda text: holder["b"].cancel("") or report)
    holder["b"] = b
    delivered = []
    with b.gathered("phone"):
        b.ask(NOTE + "close all of my browser windows", delivered.append)
    assert delivered == [report]
    assert rows("server")[-1] == {"ts": rows("server")[-1]["ts"], "channel": "phone",
                                  "said": "close all of my browser windows", "reply": report, "cut": True}
    on("pc")
    assert f'Jarvis: "{report}" (they cut it off there)' in thread.background("owner")


def test_a_spoken_reply_cut_while_it_plays_is_kept_as_far_as_he_heard_it(on, monkeypatch):
    on("pc")
    b = brain(monkeypatch, reply="Saturday is sunny and warm. Sunday brings heavy rain all day.")
    b.ask("how is the weekend looking", [].append)          # ask() returned; the voice still plays it
    b.cancel("Saturday is sunny and warm.", "Sunday brings heavy rain all day.")
    assert rows()[-1]["reply"] == "Saturday is sunny and warm." and rows()[-1]["cut"] is True
    on("server")
    text = thread.background("owner")
    assert "Sunday brings heavy rain" not in text and 'Jarvis: "Saturday is sunny and warm." (they cut it off there)' in text


def test_a_spoken_reply_cut_after_a_typed_turn_is_kept_as_far_as_he_heard_it(on, monkeypatch):
    on("pc")
    b = brain(monkeypatch)
    replying(monkeypatch, {"weekend": "Saturday is sunny and warm. Sunday brings heavy rain all day.",
                           "paint": "Paint is open, sir."})
    b.ask("how is the weekend looking", [].append)
    with b.gathered("typed"):
        b.ask([{"type": "text", "text": NOTE + "open the paint program"}], [].append)
    b.cancel("Saturday is sunny and warm.", "Sunday brings heavy rain all day.")
    voice, typed = rows()
    assert voice["reply"] == "Saturday is sunny and warm." and voice["cut"] is True
    assert typed["reply"] == "Paint is open, sir." and "cut" not in typed


def test_a_developer_verification_turn_is_never_his_thread(on, monkeypatch):
    """A 'claude-verify' device's turn (core.gateway.TEST_TURN) is not the owner's experience: never in the thread."""
    on("server")
    b = brain(monkeypatch)
    _, client = app_server(monkeypatch, lambda uid: b)
    client.post("/api/chat", json={"text": "probe question about the evening plans", "speak": False},
                headers=paired(client, device="claude-verify-probe"))
    assert rows("server") == []
    client.post("/api/chat", json={"text": "a real question about the evening plans", "speak": False},
                headers=paired(client, code="333333"))
    assert [r["said"] for r in rows("server")] == ["a real question about the evening plans"]


def test_the_acceptance_harness_turns_the_thread_off(on, monkeypatch):
    on("pc")
    monkeypatch.setenv("JARVIS_THREAD", "off")
    brain(monkeypatch).ask("open notepad and type a long paragraph about testing", [].append)
    assert rows() == []
    from pathlib import Path
    harness = Path(__file__).resolve().parent.parent / "scripts" / "gateway_acceptance.py"
    assert 'os.environ["JARVIS_THREAD"] = "off"' in harness.read_text(encoding="utf-8")


def test_deleting_chosen_awareness_lines_forgets_that_exchange_in_the_thread(on, monkeypatch):
    """His Daily Awareness page deletes chosen lines (by id): the exchange holding them leaves the thread too."""
    on("server")
    monkeypatch.setattr(pcbridge, "json_request", lambda *a, **k: {"deleted": 0})     # the PC's copy: nothing there
    b = brain(monkeypatch)
    replying(monkeypatch, {"doctor called": "I'm sorry to hear about the test results, sir.",
                           "weather": "A clear evening, sir."})
    _, client = app_server(monkeypatch, lambda uid: b)
    headers = paired(client)
    client.post("/api/chat", json={"text": "my doctor called about the test results", "speak": False}, headers=headers)
    client.post("/api/chat", json={"text": "what is the weather like this evening", "speak": False}, headers=headers)
    with users.acting_as("owner"):
        lines = awareness.entries()
    ids = [r["id"] for r in lines if "test results" in r["text"]]
    assert len(ids) == 2
    r = client.post("/api/awareness/delete", json={"ids": ids}, headers=headers).json()
    assert r["deleted"] == 2
    assert [x["said"] for x in rows("server")] == ["what is the weather like this evening"]
    on("pc")
    text = thread.background("owner")
    assert "test results" not in text and "weather like this evening" in text


def test_deleting_only_jarviss_line_still_forgets_the_exchange(on):
    on("pc")
    now = time.time()
    thread.append("owner", "tell me about my results", "Your results came back clear, sir.", "voice", now=now - 30)
    with users.acting_as("owner"):
        line = awareness.add("Jarvis", "Your results came back clear, sir.", t=now - 29)
        awareness.add("You", "unrelated words", t=now - 10)
        awareness.delete(ids=[line["id"]])
    assert rows() == []


def test_what_was_said_on_the_other_machine_mid_conversation_reaches_the_next_turn(on, monkeypatch):
    """He talks to the phone, then at the PC inside the same phone conversation's ten minutes: the phone conversation's
    next turn sees what happened at the PC (one block on that turn's message, after the cached prefix), once."""
    seen = []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        seen.append(json.dumps(history, default=str))
        return SimpleNamespace(content=[TextBlock(type="text", text="Of course, sir.")], stop_reason="end_turn")
    on("server")
    b = brain(monkeypatch)
    monkeypatch.setattr(intelligence, "request", request)
    b.context = memory.context_block
    with users.acting_as("owner"), b.gathered("phone"):
        b.ask(NOTE + "I'm heading to my desk to finish the budget spreadsheet", [].append)
    on("pc")
    thread.append("owner", "open the budget spreadsheet and email it to Marie", "Opened budget.xlsx and sent it to Marie.",
                  "voice")
    on("server")
    with users.acting_as("owner"), b.gathered("phone"):
        b.ask(NOTE + "did you send it to her already?", [].append)
    assert "sent it to Marie" in seen[-1] and "<recently-elsewhere>" in seen[-1]
    newest = b.messages[-2]["content"]
    block = next(p["text"] for p in newest if p["text"].startswith(thread.TAG))
    assert "Since this conversation began, at their PC" in block and "Bring it up only if they refer to it" in block
    assert intelligence.last_user_text(b.messages[:-1]) == "did you send it to her already?"
    with users.acting_as("owner"), b.gathered("phone"):
        b.ask(NOTE + "thanks, and what time is it in Paris?", [].append)
    assert seen[-1].count("<recently-elsewhere>") == 1, "shown once, on the turn it arrived"


def test_the_pc_shows_the_laptops_fetched_rows_when_they_are_newer_than_the_synced_file(on, monkeypatch):
    """The laptop's Syncthing sends its changes only on an hourly rescan: the PC fetches the laptop's rows itself."""
    on("server")
    thread.append("owner", "an older phone line", "Older reply.", "phone", now=time.time() - 600)
    on("pc")
    fetched = [{"ts": time.time() - 30, "channel": "phone", "said": "a phone line the synced file lacks",
                "reply": "Fresh reply, sir."}]
    monkeypatch.setattr(thread, "_remote", lambda: ("https://laptop.example", "token"))
    monkeypatch.setattr(thread, "_fetch", lambda uid: fetched if uid == "owner" else None)
    started = []
    monkeypatch.setattr(thread.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: started.append(target())))
    thread.refresh("owner")
    assert "a phone line the synced file lacks" in thread.background("owner")
    thread.refresh("owner")
    assert len(started) == 1, "at most one fetch per PULL_EVERY"


def test_after_a_forget_no_older_copy_of_the_other_machines_rows_is_shown(on, monkeypatch):
    on("server")
    thread.append("owner", "something private from the phone", "Understood, sir.", "phone", now=time.time() - 60)
    on("pc")
    monkeypatch.setattr(thread, "_remote", lambda: ("https://laptop.example", "token"))
    monkeypatch.setattr(thread, "refresh", lambda uid=None: None)       # the laptop cannot be reached just now
    assert "something private" in thread.background("owner")
    with users.acting_as("owner"):
        awareness.delete(ids=["an-id-the-laptop-holds"])                 # the laptop's delete_everywhere reached the PC
    assert thread.background("owner") == "", "the synced copy predates the forget"
    monkeypatch.setitem(thread._pulled, "owner", (time.time() + 1, []))
    assert thread.background("owner") == ""


def test_the_laptop_hands_out_only_the_callers_own_rows(on, monkeypatch):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    on("server")
    thread.append("owner", "the owner's phone line", "Yes, sir.", "phone")
    thread.append(wife, "her phone line", "Yes, madame.", "phone")
    _, client = app_server(monkeypatch, lambda uid: None)
    data = client.get("/api/thread/recent", headers=paired(client)).json()
    assert data["uid"] == "owner" and [r["said"] for r in data["rows"]] == ["the owner's phone line"]
    assert client.get("/api/thread/recent").status_code == 401


def test_the_thread_lines_say_to_leave_them_alone_unless_he_brings_them_up(on):
    on("server")
    thread.append("owner", "my budget for the graphics card is six hundred", "Noted, sir.", "phone")
    on("pc")
    assert "Bring it up only if they refer to it" in thread.background("owner")


def test_a_phone_exchange_arriving_while_the_background_is_built_is_shown_once(on, monkeypatch):
    """The newest row the conversation holds and the <background> it shows come from ONE read of the other machine's rows:
    a fetch landing between the two showed the row in the background and again, next turn, as said 'since this
    conversation began' (review 2026-11-11)."""
    seen = []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        seen.append(json.dumps(history, default=str))
        return SimpleNamespace(content=[TextBlock(type="text", text="Of course, sir.")], stop_reason="end_turn")
    on("server")
    thread.append("owner", "what's the weather tomorrow", "Sunny, sir.", "phone", now=time.time() - 300)
    on("pc")
    b = brain(monkeypatch)
    monkeypatch.setattr(intelligence, "request", request)

    def context():
        on("server")                     # the fetch / sync lands just as the background is being built
        thread.append("owner", "book the plumber for Thursday", "Booked for Thursday at nine, sir.", "phone")
        on("pc")
        return memory.context_block()
    b.context = context
    with users.acting_as("owner"):
        b.ask("good evening", [].append)
        b.context = memory.context_block
        b.ask("anything else on today?", [].append)
    assert seen[-1].count("book the plumber for Thursday") == 1, "the same phone exchange reached the model twice"


def _busy(monkeypatch, times):
    """The thread file held open by a reader that does not share delete (Syncthing hashing it, an antivirus scan): the
    rewrite's replace fails `times` times (each one is the real replace's own ~150 ms of retries), then works.
    Returns the countdown: setting its 'n' to 0 is the reader letting go."""
    from core import fileio
    real, left = fileio.replace_atomic, {"n": times}

    def replace(src, dst):
        if left["n"] > 0:
            left["n"] -= 1
            raise PermissionError(5, "Access is denied", str(dst))
        return real(src, dst)
    monkeypatch.setattr(fileio, "replace_atomic", replace)
    return left


def test_forgetting_waits_out_a_reader_holding_the_thread_file(on, monkeypatch):
    """'Forget the last ten minutes' while Syncthing hashes thread-pc.jsonl: the awareness lines went, the thread rows
    stayed (and reached the phone), and he was told it was done (review 2026-11-11)."""
    on("pc")
    thread.append("owner", "something private just now", "Understood.", "voice", now=time.time() - 60)
    _busy(monkeypatch, 3)
    with users.acting_as("owner"):
        awareness.delete(minutes=10)
    assert rows() == [] and not (storage.user_dir("owner") / "thread-pc.tmp").exists()


def test_a_forget_the_thread_could_not_do_is_never_reported_as_done(on, monkeypatch):
    from tools import today
    on("pc")
    thread.append("owner", "something private just now", "Understood.", "voice", now=time.time() - 60)
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    _busy(monkeypatch, 10 ** 6)
    with users.acting_as("owner"):
        with pytest.raises(awareness.ThreadNotForgotten):
            awareness.delete(minutes=10)
        said = today.forget_today(last_minutes=10)
    assert "Done" not in said and "again" in said, said
    assert not (storage.user_dir("owner") / "thread-pc.tmp").exists(), "no half-written copy is left in the synced folder"


def _pc_server(on, monkeypatch):
    """The PC's app server (its /api/pc/awareness), and the laptop's pcbridge relaying to it as the real one does: a
    non-200 answer is a PCError."""
    from fastapi.testclient import TestClient

    from core import appserver
    from tests.test_app import pair
    on("pc")
    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    client = TestClient(appserver.AppServer(lambda uid: None).app, raise_server_exceptions=False)
    headers = pair(client)
    answers = []

    def relay(path, method="GET", body=None):
        on("pc")
        try:
            r = client.post(path, json=body, headers=headers)
        finally:
            on("server")
        answers.append(r.status_code)
        if r.status_code != 200:
            raise pcbridge.PCError(f"The PC returned HTTP {r.status_code}.", r.status_code)
        return r.json()
    monkeypatch.setattr(pcbridge, "json_request", relay)
    return client, headers, answers


def test_a_forget_said_on_the_phone_that_the_pcs_thread_kept_is_never_reported_done(on, monkeypatch):
    """'Forget the last ten minutes' on the phone: the laptop asks the PC to delete, and the PC's thread file is held.
    The PC answered HTTP 500 and the laptop told him 'Done ... one of my machines was offline' while the PC's rows (which
    the laptop shows the phone) stayed (review 2026-11-11)."""
    from tools import today
    _, _, answers = _pc_server(on, monkeypatch)
    with users.acting_as("owner"):
        awareness.add("You", "a synthetic private line about the surprise party", "jarvis")
    thread.append("owner", "a synthetic private line about the surprise party", "Noted, sir.", "voice", now=time.time() - 60)
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    _busy(monkeypatch, 10 ** 6)
    on("server")                                                   # the laptop answers the phone
    with users.acting_as("owner"):
        said = today.forget_today(last_minutes=10)
        assert storage.load(awareness._file("pc"), []) == [], "the PC's own lines went all the same"
    assert answers == [200] and "Done" not in said and "again" in said and "offline" not in said, said


def test_a_forget_the_pcs_thread_kept_still_deletes_the_laptops_lines(on, monkeypatch):
    """The other way round: said on the PC with its thread file held, the laptop was never asked to delete its own lines,
    and he was told the transcript was deleted."""
    from tools import today, vault
    on("pc")
    thread.append("owner", "a synthetic private line about the surprise party", "Noted, sir.", "voice", now=time.time() - 60)
    asked = []
    monkeypatch.setattr(vault, "_remote", lambda: ("http://laptop.test", "synthetic"))
    monkeypatch.setattr(vault, "_call", lambda method, path, body=None, query=None: asked.append((path, body)) or {"deleted": 2})
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    _busy(monkeypatch, 10 ** 6)
    with users.acting_as("owner"):
        said = today.forget_today(last_minutes=10)
    assert [(p, b["minutes"], b["local_only"]) for p, b in asked] == [("/api/awareness/delete", 10, True)]
    assert "Done" not in said and "again" in said, said


def test_the_memory_pages_forget_says_when_a_thread_kept_the_words(on, monkeypatch):
    """The phone's Memory page deletes through the laptop's /api/awareness/delete: never a bare 500 (a silent page) or a
    plain 'Forgot ...' when a machine's thread kept the words; the page says so."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from core import appserver
    from tests.test_app import pair
    _pc_server(on, monkeypatch)
    thread.append("owner", "a synthetic private line about the surprise party", "Noted, sir.", "voice", now=time.time() - 60)
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    _busy(monkeypatch, 10 ** 6)
    on("server")
    laptop = TestClient(appserver.AppServer(lambda uid: None).app, raise_server_exceptions=False)
    r = laptop.post("/api/awareness/delete", json={"minutes": 15}, headers=pair(laptop))
    assert r.status_code == 200 and r.json()["thread_kept"] is True and r.json()["complete"] is True
    page = (Path(appserver.__file__).parents[1] / "app" / "memory.html").read_text(encoding="utf-8")
    assert page.count("kept(r)") == 3, "each of the page's deletes tells him when the words were kept"


PARTY = "a synthetic private line about the surprise party"


def test_asking_again_after_a_held_thread_file_forgets_the_minutes_first_asked(on, monkeypatch):
    """'Ask me again in a moment and I'll finish it.' Asked again a minute later, the retry's ten minutes started a
    minute later: a row from 9.5 minutes before the first ask stayed in the thread, and he was told 'Done' (review
    2026-11-11). The retry finishes what was first asked."""
    from tools import today, vault
    on("pc")
    monkeypatch.setattr(vault, "_remote", lambda: None)                 # one machine
    t0 = time.time()
    with users.acting_as("owner"):
        awareness.add("You", PARTY, "jarvis", t=t0 - 570)
    thread.append("owner", PARTY, "Noted, sir.", "voice", now=t0 - 570)
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    hold = _busy(monkeypatch, 10 ** 6)
    with users.acting_as("owner"):
        assert "again" in today.forget_today(last_minutes=10)
    assert [r["said"] for r in rows()] == [PARTY]
    hold["n"] = 0                                                       # the reader let go
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 60)             # he asks again a minute later
    with users.acting_as("owner"):
        said = today.forget_today(last_minutes=10)
    assert said.startswith("Done") and rows() == [], (said, rows())


def test_deleting_the_same_lines_again_after_the_pcs_thread_file_was_held_finishes_it(on, monkeypatch):
    """The phone's Memory page, 'Delete selected': the PC deleted the line, its thread file was held, and the page said to
    try again. The retry (the same selection) matched no line any more, so the PC's thread row stayed and the laptop kept
    showing it to the phone (review 2026-11-11)."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from core import appserver
    from tests.test_app import pair
    _pc_server(on, monkeypatch)
    with users.acting_as("owner"):
        line = awareness.add("You", PARTY, "jarvis")                    # the PC's own line
    thread.append("owner", PARTY, "Noted, sir.", "voice", now=time.time() - 60)
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    hold = _busy(monkeypatch, 10 ** 6)
    on("server")                                                        # the laptop serves the phone's page
    laptop = TestClient(appserver.AppServer(lambda uid: None).app, raise_server_exceptions=False)
    headers = pair(laptop)
    first = laptop.post("/api/awareness/delete", json={"ids": [line["id"]]}, headers=headers).json()
    assert first["thread_kept"] is True and first["deleted"] == 1
    hold["n"] = 0
    again = laptop.post("/api/awareness/delete", json={"ids": [line["id"]]}, headers=headers).json()
    assert not again.get("thread_kept") and rows("pc") == [], (again, rows("pc"))
    with users.acting_as("owner"):
        assert "surprise party" not in thread.background("owner")
    page = (Path(appserver.__file__).parents[1] / "app" / "memory.html").read_text(encoding="utf-8")
    assert "if (!r.thread_kept) sel.clear()" in page, "the page keeps his selection, so pressing Delete again finishes it"


LATER = ["open the synthetic render folder", "how is the synthetic castle scene lit"]


@pytest.mark.parametrize("then", ["an unrelated line deleted", "the last ten minutes forgotten"])
def test_a_forget_the_held_file_blocked_never_takes_what_was_said_after_it(on, monkeypatch, then):
    """A forget the held thread file blocked was kept with no end: whatever forget came next on that machine (one
    unrelated Memory-page line, 'forget the last ten minutes' an hour later) dropped every row said since it, the rows
    the other machine's background needs, while their awareness lines stayed (review 2026-11-11)."""
    from tools import today, vault
    on("pc")
    monkeypatch.setattr(vault, "_remote", lambda: None)                 # one machine
    real = time.time
    t0 = real() - 3600
    thread.append("owner", PARTY, "Noted, sir.", "voice", now=t0 - 300)
    monkeypatch.setattr(time, "time", lambda: t0)                      # an hour ago: 'forget the last ten minutes'
    monkeypatch.setattr(thread, "PATIENT_S", 0.3, raising=False)
    hold = _busy(monkeypatch, 10 ** 6)
    with users.acting_as("owner"):
        assert "again" in today.forget_today(last_minutes=10)
    hold["n"] = 0                                                       # the reader let go; he never asked again
    monkeypatch.setattr(time, "time", real)
    thread.append("owner", LATER[0], "Opened it, sir.", "voice", now=t0 + 1200)
    thread.append("owner", LATER[1], "Two area lights, sir.", "voice", now=t0 + 2400)
    with users.acting_as("owner"):
        if then == "an unrelated line deleted":
            lunch = awareness.add("You", "a synthetic line about lunch plans", "jarvis", t=real() - 30)
            awareness.delete(ids=[lunch["id"]])
        else:
            today.forget_today(last_minutes=10)                         # the rows above are 20 and 40 minutes old
    assert [r["said"] for r in rows()] == LATER, rows()                 # the owed forget finished, nothing said since


def test_the_other_machines_exchanges_stay_within_their_budget_in_the_background(on):
    """The block sits in the local model's pinned system message outside the core budget (core.contextfit): six exchanges
    at full length were ~530 tokens, past the ~500 documented. The oldest go first; the newest is always shown."""
    on("server")
    now = time.time()
    for n in range(thread.LIMIT):
        thread.append("owner", f"question {n} " + "about the long drive and the tyres " * 8,
                      f"answer {n} " + "the car will be fine for the trip, sir " * 8, "phone", now=now - 600 + n)
    on("pc")
    block = thread.background("owner")
    assert len(block) <= thread.BACKGROUND_CHARS and f"question {thread.LIMIT - 1}" in block
    assert "question 0" not in block


# ---- cross-device audit C (2026-11-13): the laptop learns the PC's recent voice turns promptly -----------------------------
def _threads_run_inline(monkeypatch):
    monkeypatch.setattr(thread.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: target()))


def test_the_pc_route_hands_the_owner_only_the_pcs_own_rows(on, monkeypatch):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    on("pc")
    thread.append("owner", "a voice line at the desk", "Of course, sir.", "voice")
    thread.append(wife, "her voice line", "Of course, madame.", "voice")
    _, client = app_server(monkeypatch, lambda uid: None)
    data = client.get("/api/pc/thread", headers=paired(client)).json()
    assert data["uid"] == "owner" and [r["said"] for r in data["rows"]] == ["a voice line at the desk"]
    assert client.get("/api/pc/thread").status_code == 401                    # the same auth as its neighbours
    on("server")
    assert client.get("/api/pc/thread", headers=paired(client, "Pad", "333333")).status_code == 503   # only the PC holds those rows


def test_the_laptop_fetches_the_pcs_rows_over_the_pc_link_and_shows_them(on, monkeypatch):
    on("pc")
    thread.append("owner", "an older desk line", "Older reply.", "voice", now=time.time() - 600)
    on("server")
    fetched = {"uid": "owner", "rows": [{"ts": time.time() - 20, "channel": "voice", "said": "a desk line the synced file lacks",
                                         "reply": "Fresh reply, sir."}]}
    asked = []

    def link(path, method="GET", body=None):
        asked.append((path, method, users.current()))
        return fetched
    monkeypatch.setattr(pcbridge, "json_request", link)
    monkeypatch.setattr(pcbridge, "_address", lambda: ("https://pc.example", "token"))
    _threads_run_inline(monkeypatch)
    thread.refresh("owner")
    assert asked == [("/api/pc/thread", "GET", "owner")]
    assert "a desk line the synced file lacks" in thread.background("owner")
    thread.refresh("owner")
    assert len(asked) == 1, "at most one fetch per PULL_EVERY"


def test_the_laptop_stays_silent_when_the_pc_is_asleep(on, monkeypatch):
    on("server")
    monkeypatch.setattr(pcbridge, "_address", lambda: ("https://pc.example", "token"))

    def asleep(path, method="GET", body=None):
        raise pcbridge.PCError("The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.")
    monkeypatch.setattr(pcbridge, "json_request", asleep)
    _threads_run_inline(monkeypatch)
    thread.refresh("owner")                                                      # no exception, nothing shown
    assert thread.recent("owner") == [] and "owner" not in thread._pulled


def test_the_laptop_with_no_pc_link_never_tries(on, monkeypatch):
    on("server")

    def unset():
        raise pcbridge.PCError("The PC link needs JARVIS_PC_URL and JARVIS_PC_TOKEN on the laptop.")
    monkeypatch.setattr(pcbridge, "_address", unset)
    monkeypatch.setattr(pcbridge, "json_request", lambda *a, **k: pytest.fail("no link configured"))
    _threads_run_inline(monkeypatch)
    thread.refresh("owner")


def test_a_household_members_rows_are_never_taken_from_the_owners_link(on, monkeypatch):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    on("server")
    monkeypatch.setattr(pcbridge, "_address", lambda: ("https://pc.example", "token"))
    monkeypatch.setattr(pcbridge, "json_request", lambda *a, **k: pytest.fail("her thread is not on the owner's link"))
    _threads_run_inline(monkeypatch)
    thread.refresh(wife)


def test_the_pc_link_fetch_uses_the_short_timeout():
    assert thread.PULL_TIMEOUT == 4.0
    assert pcbridge.SLOW_TIMEOUTS["/api/pc/thread"] == thread.PULL_TIMEOUT
