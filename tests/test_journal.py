"""The journal: summaries, facts about the person, lessons from mistakes, humor notes (model faked)."""
import json

from core import journal, storage, users
from tools import memory

CONVO = [
    {"role": "user", "content": [{"type": "text", "text": "<background>\nstuff\n</background>"},
                                 {"type": "text", "text": "uninstall all proton apps"}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "uninstall_program", "input": {"name": "Proton Drive"}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "Couldn't remove Proton Drive yet."}]},
    {"role": "assistant", "content": [{"type": "text", "text": "Done on the second try, sir."}]},
    {"role": "user", "content": "lol finally. my password is hunter2 by the way"},
    {"role": "assistant", "content": "Noted, though I'd rather not."},
]


def test_transcript_skips_background_and_counts_turns():
    text, turns = journal.transcript(CONVO)
    assert "stuff" not in text and "USER: uninstall all proton apps" in text
    assert "(Jarvis used uninstall_program" in text and "(result: Couldn't remove" in text
    assert turns == 2


def test_archive_saves_summary_facts_lessons_humor(monkeypatch):
    monkeypatch.setattr(journal, "_ask", lambda prompt: {
        "summary": "Uninstalled the Proton apps after the first method failed.",
        "open": ["Check Proton VPN is gone"],
        "facts": [{"fact": "Prefers Jarvis to just do tasks without asking twice.", "category": "preference"},
                  {"fact": "His password is hunter2", "category": "personal"}],
        "lessons": ["Proton apps are per-user installs: use their own uninstaller."],
        "humor": ["Laughs at Jarvis's dry understatement."]})
    journal.archive("owner", CONVO)
    with users.acting_as("owner"):
        entry = journal.recent()[-1]
        facts = [m["fact"] for m in storage.load(memory.FILE, [])]
    assert entry["summary"].startswith("Uninstalled the Proton apps") and entry["open"] == ["Check Proton VPN is gone"]
    assert "Prefers Jarvis to just do tasks without asking twice." in facts
    assert not any("hunter2" in f for f in facts)                       # secrets are never written down
    assert "Humor: Laughs at Jarvis's dry understatement." in facts
    assert "Proton apps are per-user installs: use their own uninstaller." in journal.lessons()
    assert journal.add_lesson("Proton apps are per-user installs: use their own uninstaller!") == "Already learned."
    with users.acting_as("owner"):
        assert journal.search("proton")[0]["summary"].startswith("Uninstalled")
        ctx = memory.context_block()
    assert "recent conversations" in ctx and "Lessons you've learned" in ctx and "Check Proton VPN is gone" in ctx


def test_short_chats_are_not_journaled(monkeypatch):
    monkeypatch.setattr(journal, "_ask", lambda p: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert journal.archive("owner", [{"role": "user", "content": "what time is it"},
                                     {"role": "assistant", "content": "Ten past nine."}]) is None


def test_private_per_person(monkeypatch):
    monkeypatch.setattr(journal, "_ask", lambda p: {"summary": "Talked about a surprise party.", "open": [],
                                                    "facts": [], "lessons": [], "humor": []})
    jo = users.add("Maya", {"en": "madame"})
    journal.archive(jo, CONVO)
    with users.acting_as("owner"):
        assert not any("surprise party" in e["summary"] for e in journal.recent(50))


# -- the local model writes the notes for free; only what they actually said is kept ------------------------------------
LOCAL_CONVO = [
    {"role": "user", "content": [{"type": "text", "text": "<background>\nstuff\n</background>"},
                                 {"type": "text", "text": "My wife Maya and I are planning a trip to Miami to see my sister Marie in December."}]},
    {"role": "assistant", "content": "Lovely. Shall I note the dates?"},
    {"role": "user", "content": "Not yet. Also, stop saying 'one sec' before every answer, it's annoying."},
    {"role": "assistant", "content": "Understood, sir."},
]


def _notes(**over):
    notes = {"summary": "They are planning a December trip to Miami to see their sister Marie.",
             "open": ["trip to Miami in December", "book the flights to Mars"],
             # each fact quotes the words of theirs that state it (NOTES_SCHEMA requires "evidence"); the Corvette quote
             # is the model's own invention and is in none of their lines
             "facts": [{"fact": "His wife is Maya.", "category": "family", "evidence": "My wife Maya"},
                       {"fact": "His sister Marie lives in Miami.", "category": "family",
                        "evidence": "a trip to Miami to see my sister Marie"},
                       {"fact": "He drives a red Corvette.", "category": "other", "evidence": "I drive a red Corvette"}],
             "lessons": ["Don't open answers with 'one sec'.", "Always restart the router first."]}
    notes.update(over)
    return notes


def _local_model(monkeypatch, notes):
    from types import SimpleNamespace
    from anthropic.types import TextBlock
    from core import intelligence
    seen = []

    def request(role, system, history, tools, cancelled=lambda: False, response_format=None, on_text=None):
        seen.append((role, response_format))
        return SimpleNamespace(content=[TextBlock(type="text", text=json.dumps(notes))], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    return seen


def test_notes_object_accepts_a_complete_object_wrapped_in_model_prose():
    notes = {"summary": "A valid note.", "open": [], "facts": [], "lessons": []}
    assert journal._notes_object("```json\n" + json.dumps(notes) + "\n```\nDone.") == notes


def test_notes_object_rejects_malformed_or_non_object_model_output():
    assert journal._notes_object('{"summary": "cut off"') == {}
    assert journal._notes_object('["not", "notes"]') == {}


def test_local_notes_keep_only_what_they_said(monkeypatch):
    seen = _local_model(monkeypatch, _notes())
    users.set_current("owner")
    entry = journal.archive_local("owner", LOCAL_CONVO)
    assert seen[0][0] == "advanced" and seen[0][1]["json_schema"]["schema"] == journal.NOTES_SCHEMA
    assert entry["source"] == "local_model" and entry["summary"].endswith("(local model's note)")
    assert entry["open"] == ["trip to Miami in December"]                 # Mars was never mentioned
    facts = [m["fact"] for m in storage.load(memory.FILE, [])]
    # named in their own words first; the model's "His wife is Maya." is the same fact (not stored again) and
    # "His sister Marie lives in Miami." says more than "Their sister is Marie." (replaces it); no Corvette
    assert facts == ["Their wife is Maya.", "His sister Marie lives in Miami."]
    assert journal.lessons() == ["Don't open answers with 'one sec'."]      # they complained about that, not routers


def test_local_notes_lessons_need_a_correction_in_their_words(monkeypatch):
    _local_model(monkeypatch, _notes())
    users.set_current("owner")
    calm = LOCAL_CONVO[:2] + [{"role": "user", "content": "Not yet, thanks."}, LOCAL_CONVO[3]]
    journal.archive_local("owner", calm)
    assert journal.lessons() == []


def test_without_a_local_tier_the_plain_record_is_kept(monkeypatch):
    from core import intelligence

    def down(*a, **k):
        raise intelligence.LocalFailure("configured_model_unavailable")
    monkeypatch.setattr(intelligence, "request", down)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    users.set_current("owner")
    entry = journal.archive_local("owner", LOCAL_CONVO)
    assert entry["source"] == "local_extractive" and entry["summary"].startswith("Topics requested")
    assert "Miami" in entry["summary"]
    assert [m["fact"] for m in storage.load(memory.FILE, [])] == ["Their wife is Maya.", "Their sister is Marie."]


def test_a_summary_of_some_other_conversation_is_not_kept(monkeypatch):
    _local_model(monkeypatch, _notes(summary="Discussed quarterly tax filings for the bakery.", facts=[], lessons=[], open=[]))
    users.set_current("owner")
    entry = journal.archive_local("owner", LOCAL_CONVO)
    assert entry["source"] == "local_extractive"


def test_people_they_name_are_remembered_whatever_the_model_noted(monkeypatch):
    """The 3B model's notes kept the sister and dropped "my wife Maya" from the same sentence."""
    assert journal.stated_relations("So my wife Maya and I are thinking of driving down to Miami to see my sister Marie.") \
        == ["Their wife is Maya.", "Their sister is Marie."]
    assert journal.stated_relations("my son Noah-James starts school; my dog Biscuit is old") == ["Their son is Noah-James.", "Their dog is Biscuit."]
    assert journal.stated_relations("my sister lives in Miami and my wife is coming") == []       # no name given
    assert journal.stated_relations("My boss Probably won't like it") == []                        # not a name
    _local_model(monkeypatch, _notes(facts=[], lessons=[], open=[]))
    users.set_current("owner")
    journal.archive_local("owner", LOCAL_CONVO)
    assert "Their wife is Maya." in [m["fact"] for m in storage.load(memory.FILE, [])]


def test_local_notes_take_the_stronger_tier_first(monkeypatch):
    seen = _local_model(monkeypatch, _notes())
    users.set_current("owner")
    journal.archive_local("owner", LOCAL_CONVO)
    assert seen[0][0] == "advanced"


# -- evidence: a fact the model extracts must quote his own words ---------------------------------------------------------
# His real line (owner-cases #415, 2026-10-30): the model had invented "she's getting ready to put the kids in school".
SCHOOL = ("I need you to learn something that when it's not morning, don't say good morning. And my wife and I, we only "
          "have one child. Don't say she's getting ready to put the kids in school if she didn't tell you that. Where did "
          "you get that information? Don't do that.")
SCHOOL_CONVO = [
    {"role": "user", "content": "My wife Maya is off today."},
    {"role": "assistant", "content": "Lovely, sir. She's getting the kids ready for school, I take it."},
    {"role": "user", "content": SCHOOL},
    {"role": "assistant", "content": "Understood, sir."},
]


def test_a_fact_is_kept_only_with_a_literal_quote_of_his_words(monkeypatch):
    _local_model(monkeypatch, _notes(open=[], lessons=[], facts=[
        {"fact": "He and his wife have one child.", "category": "family",
         "evidence": "my wife and I, we only have one child"},                      # his words, verbatim: kept
        {"fact": "His wife takes the kids to school.", "category": "family",
         "evidence": "she's getting ready to put the kids in school"},              # he said NOT to say it
        {"fact": "His wife gets the kids ready for school.", "category": "family",
         "evidence": "getting the kids ready for school"},                          # only Jarvis said that
        {"fact": "His wife Maya is off today.", "category": "family"},           # no quote at all
        {"fact": "Maya has the day off.", "category": "family",
         "evidence": "Maya has the day off"},                                    # a paraphrase, not his words
    ]))
    users.set_current("owner")
    journal.archive_local("owner", SCHOOL_CONVO)
    rows = storage.load(memory.FILE, [])
    facts = [m["fact"] for m in rows]
    assert "He and his wife have one child." in facts
    assert not any("school" in f.lower() or "day off" in f or "off today" in f for f in facts)
    kept = next(m for m in rows if m["fact"] == "He and his wife have one child.")
    assert kept["source"] == "said" and kept["evidence"] == "my wife and I, we only have one child"
    named = next(m for m in rows if m["fact"] == "Their wife is Maya.")      # people he named: his words as evidence
    assert named["source"] == "said" and named["evidence"] == "My wife Maya"


def test_quoted_evidence_rules():
    lines = ["My wife and I, we only have one child.", "I drive a red Corvette now"]
    assert journal.quoted("my wife and I, we only have one child", lines)
    assert journal.quoted("“I drive a  red Corvette”", lines)          # curly quotes, case and spacing aside
    assert not journal.quoted("Corvette", lines)                                    # one word proves nothing
    assert not journal.quoted("I drive a blue Corvette", lines)
    assert not journal.quoted("getting ready to put the kids in school", [SCHOOL])  # he said not to say it
    assert journal.quoted("we only have one child", [SCHOOL])


def test_the_schema_asks_the_model_for_evidence():
    item = journal.NOTES_SCHEMA["properties"]["facts"]["items"]
    assert "evidence" in item["required"] and item["properties"]["evidence"] == {"type": "string"}
    assert '"evidence"' in journal.PROMPT


def test_a_genuine_quote_must_state_the_fact_not_just_share_two_words():
    """'my wife and I, we only have one child' carried 'His wife takes their child to school every morning.' (two
    shared words), stored as 'you said'."""
    quote = "my wife and I, we only have one child"
    lines = ["My wife and I, we only have one child."]
    for invented in ("His wife takes their child to school every morning.", "His wife is expecting their second child.",
                     "His wife wants another child next year."):
        assert not journal._evidenced({"fact": invented, "evidence": quote}, lines), invented
    assert journal._evidenced({"fact": "He and his wife have one child.", "evidence": quote}, lines)
    assert journal._evidenced({"fact": "His sister Marie lives in Miami.", "evidence": "see my sister Marie in Miami"},
                              ["We're going to see my sister Marie in Miami."])      # a paraphrase's verb is allowed



def test_his_name_and_jarvis_are_who_a_fact_is_about_not_what_it_says():
    """Live qwen3.5:9b, 2026-11-10: 'Alex prefers Jarvis to stop ending answers with questions.' quoting his own line
    was dropped in 3 runs of 3, because 'Alex' and 'Jarvis' are not words he said."""
    line = "and stop ending every answer with a question, I hate that"
    for fact in ("Alex prefers Jarvis to stop ending answers with questions.",
                 "Alex dislikes when Jarvis ends his answers with questions."):
        assert journal._evidenced({"fact": fact, "evidence": line}, [line], "Alex"), fact
    # the one paraphrase word never flips how he feels
    assert not journal._evidenced({"fact": "Alex likes when Jarvis ends answers with questions.", "evidence": line},
                                  [line], "Alex")
    assert not journal._evidenced({"fact": "Alex hates coffee in the morning.", "evidence": "I love coffee in the morning"},
                                  ["I love coffee in the morning."], "Alex")
    # and it is no loophole for invented details: the rest of the fact must still be his words
    assert not journal._evidenced({"fact": "Alex wants Jarvis to read the news every morning.", "evidence": line},
                                  [line], "Alex")

def test_words_he_denied_or_asked_about_are_no_evidence(monkeypatch):
    """'Don't say she's getting ready to put the kids in school ...': evidence three words after 'don't' was taken as
    his statement, stored as 'you said', and closed 'Their wife is Maya.' through the wife slot."""
    for evidence in ("ready to put the kids in school", "put the kids in school", "she's getting ready to put the kids"):
        assert not journal.quoted(evidence, [SCHOOL]), evidence
    assert not journal.quoted("my wife is pregnant", ["Did I tell you my wife is pregnant?"])
    assert not journal.quoted("my wife is pregnant", ["did I tell you my wife is pregnant"])     # no "?" from STT
    assert not journal.quoted("she's getting ready to put the kids in school",
                              ["You made up that she's getting ready to put the kids in school."])
    assert journal.quoted("my wife is pregnant", ["Can you remember that my wife is pregnant?"])  # asked to keep it
    assert journal.quoted("my wife Maya is off", ["I'm not working today, but my wife Maya is off too."])
    assert journal.quoted("we only have one child", [SCHOOL])
    memory.remember("Their wife is Maya.", "family", False)
    _local_model(monkeypatch, _notes(open=[], lessons=[], facts=[
        {"fact": "His wife is getting ready to put the kids in school.", "category": "family",
         "evidence": "ready to put the kids in school"}]))
    users.set_current("owner")
    journal.archive_local("owner", SCHOOL_CONVO)
    facts = [m["fact"] for m in storage.load(memory.FILE, [])]
    assert "Their wife is Maya." in facts and not any("school" in f for f in facts)


def test_a_plan_is_no_evidence_for_a_fact_that_is_true_now(monkeypatch):
    """'I'm going to live in Denver next month.' -> 'They live in Denver.' (evidence 'live in Denver') closed his
    current home through the home slot."""
    memory.remember("They live in Springfield.", "personal", False)
    convo = [{"role": "user", "content": "I'm going to live in Denver next month."},
             {"role": "assistant", "content": "Exciting, sir."},
             {"role": "user", "content": "Yeah, the new job starts in May."},
             {"role": "assistant", "content": "Noted."}]
    _local_model(monkeypatch, _notes(summary="He is going to live in Denver next month for the new job.", open=[],
                                     lessons=[], facts=[{"fact": "They live in Denver.", "category": "personal",
                                                         "evidence": "live in Denver"}]))
    users.set_current("owner")
    journal.archive_local("owner", convo)
    facts = [m["fact"] for m in storage.load(memory.FILE, [])]
    assert facts == ["They live in Springfield."]
    assert journal._evidenced({"fact": "Plans to move to Denver next month.", "evidence": "live in Denver next month"},
                              ["I'm going to live in Denver next month."]) is False   # "move"/"plans" are not his words
    assert journal._evidenced({"fact": "Is going to live in Denver next month.", "evidence": "going to live in Denver next month"},
                              ["I'm going to live in Denver next month."])


def test_the_local_notes_wait_for_a_gap_and_a_game_gets_the_plain_record(monkeypatch):
    """Background model work (core/inferencegate): during a game no model call is made; waiting runs out and the entry
    is still written, as the plain record of what was asked."""
    from core import inferencegate
    seen = _local_model(monkeypatch, _notes())
    monkeypatch.setattr(inferencegate, "GAMING", lambda: True)
    monkeypatch.setattr(inferencegate, "POLL_S", 0.05)
    users.set_current("owner")
    entry = journal.archive_local("owner", LOCAL_CONVO, wait=0.2)
    assert seen == [] and entry["source"] == "local_extractive"
    monkeypatch.setattr(inferencegate, "GAMING", lambda: False)
    assert journal.archive_local("owner", LOCAL_CONVO)["source"] == "local_model" and seen


def test_an_entry_written_after_a_game_is_dated_when_the_conversation_was_archived(monkeypatch):
    """The notes wait for the card (core/inferencegate), up to hours through a game; the entry was stamped when the wait
    ended, so 'when did we talk about the bike?' got the end of the game, and several such entries fell in any order."""
    from contextlib import contextmanager
    from datetime import datetime as real
    from core import inferencegate

    clock = {"now": real(2026, 11, 10, 19, 25)}

    class Clock(real):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]
    monkeypatch.setattr(journal, "datetime", Clock)

    @contextmanager
    def a_long_game(name, timeout=None):
        clock["now"] = real(2026, 11, 10, 22, 40)                      # the game ends; the slot is free at last
        yield inferencegate.Slot(name, True)
    monkeypatch.setattr(inferencegate, "background", a_long_game)
    _local_model(monkeypatch, _notes())
    users.set_current("owner")
    entry = journal.archive_local("owner", LOCAL_CONVO)
    assert entry["t"] == "2026-11-10T19:25"


def test_a_phone_conversation_on_the_laptop_is_journaled_in_its_own_file(monkeypatch):
    """Server role, end to end with a fake model: two phone messages through /api/chat, the conversation ends, and the
    laptop's journal-server.json gets the entry (2026-11-11: the PC's copy had looked silent since Nov 6; the laptop had
    journaled every phone conversation, and Syncthing on the PC had stopped: see core/syncwatch). The PC, which reads the
    synced files, then shows the phone's exchanges in its next background."""
    from types import SimpleNamespace

    from anthropic.types import TextBlock
    from fastapi.testclient import TestClient

    from brain import llm
    from core import appserver, intelligence, pcbridge, pcfast, quick
    from tools import voice
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)       # the laptop (JARVIS_ROLE=server)
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    notes = {"summary": "They weighed up buying a new graphics card for gaming and settled on a budget.",
             "open": [], "facts": [], "lessons": []}

    def request(role, system, history, tools, cancelled, on_text=None, response_format=None, **kw):
        text = json.dumps(notes) if response_format else "A sensible budget, sir."
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)

    def make_brain(uid):                                  # as server.py makes them
        b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid=uid)
        b.context = memory.context_block
        for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
            monkeypatch.setattr(b, shortcut, lambda text, emit: False)
        return b
    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    server = appserver.AppServer(make_brain)
    client = TestClient(server.app)
    voice.pair_codes["222222"] = "owner"
    token = client.post("/api/pair", json={"code": "222222", "device": "phone"}).json()["token"]
    for text in ("I'm thinking about buying a new graphics card for gaming", "my budget for the graphics card is 600"):
        r = client.post("/api/chat", json={"text": text, "speak": False}, headers={"Authorization": f"Bearer {token}"})
        assert r.json()["reply"] == "A sensible budget, sir."
    b = server._brain("owner")
    assert b.archive_if_idle(force=True)                  # what the journal sweeper does once the conversation ends
    b._archiving.join(10)
    with users.acting_as("owner"):
        written = storage.load("journal-server.json", [])
        assert storage.load("journal-pc.json", []) == []
    assert written and written[-1]["source"] == "local_model" and "graphics card" in written[-1]["summary"]
    # the PC reads the synced files: its next conversation knows what was just said on the phone
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    with users.acting_as("owner"):
        ctx = memory.context_block(core_only=True)
    assert "Recently on their phone" in ctx and "budget for the graphics card is 600" in ctx
