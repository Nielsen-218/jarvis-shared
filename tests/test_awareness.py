"""Daily Awareness (Issue #7): retention, search, recall, deletion, user separation, consent, bystanders."""
from datetime import datetime

import numpy as np
import pytest

from core import awareness, users

NOW = datetime(2026, 10, 22, 14, 0).timestamp()   # 2 pm


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(awareness, "_now", lambda: NOW)
    monkeypatch.setattr(awareness, "_host", lambda: "pc")


def at(h, m=0, day=22):
    return datetime(2026, 10, day, h, m).timestamp()


# -- the transcript ------------------------------------------------------------------------------
def test_lines_older_than_24_hours_are_dropped_and_pruned():
    awareness.add("You", "yesterday lunch", t=at(12, 0, day=21))     # 26 h ago
    awareness.add("You", "this morning", t=at(8, 0))
    assert [r["text"] for r in awareness.entries()] == ["this morning"]
    from core import storage
    assert len(storage.load("awareness-pc.json", [])) == 1             # pruned from disk on write, not just hidden


def test_noise_ignored_lines_and_secrets_never_land():
    assert awareness.add("Guest", "Thank you.", "room") is None          # Whisper's silence hallucination
    assert awareness.add("Jarvis", "[ignore] not for me") is None
    e = awareness.add("You", "my password is hunter2 and my card 4111111111111111")
    assert "hunter2" in e["text"] and "4111111111111111" not in e["text"] and "[hidden]" in e["text"]
    assert awareness.add("You", "Thank you.", "jarvis")                  # said TO Jarvis: real, kept


def test_search_and_time_window():
    awareness.add("You", "we should rotate the tires", t=at(8, 5))
    awareness.add("Jarvis", "The tire shop opens at nine", t=at(8, 6))
    awareness.add("You", "order pizza tonight", t=at(13, 0))
    assert len(awareness.search("tire")) == 2            # "tire" also finds "tires"
    assert [r["text"] for r in awareness.search("tires")] == ["we should rotate the tires"]
    assert [r["text"] for r in awareness.search("pizza tonight")] == ["order pizza tonight"]   # all words
    assert [r["t"] for r in awareness.window(at(8), at(9))] == [at(8, 5), at(8, 6)]


def test_entries_from_both_machines_are_merged(monkeypatch):
    awareness.add("You", "said on the PC", t=at(9))
    monkeypatch.setattr(awareness, "_host", lambda: "server")
    awareness.add("You", "said on the phone", t=at(10))
    assert [r["text"] for r in awareness.entries()] == ["said on the PC", "said on the phone"]


# -- recall --------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, start, end", [
    ("8 am", (7, 35), (8, 25)), ("8 this morning", (7, 35), (8, 25)), ("around 1:30 pm", (13, 5), (13, 55)),
    ("at 1", (12, 35), (13, 25)),          # no am/pm at 2 pm: the most recent 1 o'clock
    ("noon", (11, 35), (12, 25)),
])
def test_parse_when(said, start, end):
    s, e = awareness.parse_when(said, now=datetime.fromtimestamp(NOW))
    assert (datetime.fromtimestamp(s).hour, datetime.fromtimestamp(s).minute) == start
    assert (datetime.fromtimestamp(e).hour, datetime.fromtimestamp(e).minute) == end


def test_parse_when_relative_and_parts_of_day():
    now = datetime.fromtimestamp(NOW)
    s, e = awareness.parse_when("an hour ago", now=now)
    assert s < at(13) < e
    s, e = awareness.parse_when("this morning", now=now)
    assert (s, e) == (at(6), at(12))
    s, _ = awareness.parse_when("11 pm", now=now)                       # in the future today -> last night
    assert datetime.fromtimestamp(s).day == 21
    assert awareness.parse_when("purple", now=now) is None


def test_recall_returns_grounded_timestamped_lines():
    awareness.add("Alex", "remind me to call the dentist", t=at(8, 2))
    awareness.add("Jarvis", "I'll remind you at noon", t=at(8, 3))
    awareness.add("Alex", "unrelated later chat", t=at(11))
    out = awareness.recall("8 this morning")
    assert "08:02 Alex: remind me to call the dentist" in out and "08:03 Jarvis" in out
    assert "unrelated" not in out and "Answer ONLY from these lines" in out


def test_recall_says_honestly_when_there_is_nothing():
    awareness.add("You", "lunch plans", t=at(12, 30))
    out = awareness.recall("8 am")
    assert out.startswith("I don't have anything between 07:35 and 08:25")
    assert "room listening (Daily Awareness) was off then" in out and "Don't guess" in out
    assert "couldn't work out" in awareness.recall("purple o'clock")


@pytest.mark.parametrize("asked, hit", [
    ("What were we just talking about a minute ago?", True),
    ("what did I say at 8 this morning?", True),
    ("Remind me what we discussed earlier", True),
    ("what did we talk about yesterday about the car?", False),   # older than today: the vault / journal
    ("what's the weather at 8?", False),                          # not about the conversation
    ("turn on the lights", False),
])
def test_context_for_spots_questions_about_todays_conversation(asked, hit):
    awareness.add("You", "the plumber comes Thursday", t=at(13, 59))
    assert (awareness.context_for(asked) is not None) == hit


def test_context_for_just_now_gives_the_recent_lines_without_echoing_the_question():
    awareness.add("You", "old morning chat", t=at(8))
    awareness.add("You", "the plumber comes Thursday at 3", t=at(13, 58))
    awareness.add("Jarvis", "Noted, Thursday at 3", t=at(13, 58))
    awareness.add("You", "What were we just talking about?", t=at(13, 59))
    out = awareness.context_for("What were we just talking about?")
    assert "13:58 You: the plumber comes Thursday at 3" in out and "13:58 Jarvis" in out
    assert "old morning chat" not in out and "13:59" not in out


def test_leaked_tool_call_text_is_rejected_not_spoken():
    from core import intelligence
    leaked = '{"name": "recall_memories", "parameters": {"keywords": "minute ago"}}'
    response = {"choices": [{"finish_reason": "stop", "message": {"content": leaked}}]}
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_reply(response, allowed=set())
    ok = {"choices": [{"finish_reason": "stop", "message": {"content": '"We were talking about the plumber.'}}]}
    assert intelligence.parse_reply(ok, allowed=set()).content[0].text == "We were talking about the plumber."
    ok["choices"][0]["message"]["content"] = '"Noted, sir."'
    assert intelligence.parse_reply(ok, allowed=set()).content[0].text == "Noted, sir."
    ok["choices"][0]["message"]["content"] = 'He said "hi" to you.'
    assert intelligence.parse_reply(ok, allowed=set()).content[0].text == 'He said "hi" to you.'


def test_journal_keeps_only_what_the_user_typed():
    from core import journal
    journal.archive_local("owner", [{"role": "user", "content": [
        {"type": "text", "text": "<daily-awareness>\nTranscript ... 12:48 You: secret plans\n</daily-awareness>"},
        {"type": "text", "text": "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
                                 "What were we just talking about?"}]}])
    summary = journal.recent(1)[-1]["summary"]
    assert summary.endswith("What were we just talking about?") and "Transcript" not in summary and "[Sent" not in summary


# -- deletion ------------------------------------------------------------------------------------
def test_delete_by_id_last_minutes_and_everything():
    a = awareness.add("You", "keep me", t=at(9))
    b = awareness.add("You", "delete me", t=at(13, 50))
    awareness.add("You", "delete me too", t=at(13, 55))
    assert awareness.delete(ids=[a["id"]]) == 1
    assert awareness.delete(minutes=15) == 2 and awareness.entries() == []
    awareness.add("You", "x", t=at(10)); awareness.add("You", "y", t=at(11))
    assert awareness.delete(everything=True) == 2 and awareness.entries() == []
    assert b  # (silence unused warning)


def test_delete_only_touches_this_machines_file(monkeypatch):
    awareness.add("You", "on the pc", t=at(9))
    monkeypatch.setattr(awareness, "_host", lambda: "server")
    awareness.add("You", "on the server", t=at(10))
    assert awareness.delete(everything=True) == 1                         # the server's own file only
    assert [r["text"] for r in awareness.entries()] == ["on the pc"]


# -- separation, session, consent ------------------------------------------------------------------
def test_each_person_sees_only_their_own_day():
    wife = users.add("Marie", {"en": "madame"}, "wife")
    awareness.add("You", "owner's private chat", t=at(9))
    with users.acting_as(wife):
        awareness.add("Marie", "her own chat", t=at(9, 30))
        assert [r["text"] for r in awareness.entries()] == ["her own chat"]
        assert "owner's private chat" not in awareness.recall()
    assert [r["text"] for r in awareness.entries()] == ["owner's private chat"]


def test_starting_room_listening_needs_consent_and_tracks_periods(monkeypatch):
    with pytest.raises(ValueError, match="knows Jarvis is listening"):
        awareness.set_session("start")
    s = awareness.set_session("start", consent=True)
    assert s["state"] == "on" and not s["guests"] and s["periods"] == [[NOW, None]]
    monkeypatch.setattr(awareness, "_now", lambda: NOW + 600)
    assert awareness.set_session("pause")["state"] == "paused"
    assert awareness.listening_at(NOW + 300) and not awareness.listening_at(NOW + 900)
    assert awareness.set_session("resume")["state"] == "on"
    assert awareness.set_session("stop")["state"] == "off"


# -- room listening: who gets recorded -------------------------------------------------------------
class _VoiceID:
    enrolled = True
    thresholds = {}

    def __init__(self, prints):
        self.prints = prints

    def embed(self, audio):
        return audio[:2] / np.linalg.norm(audio[:2])


def _room(voice, guests=False):
    r = awareness.RoomListener(listener=None, stt=None, voiceid=voice, idle=lambda: True)
    r._guests = guests
    return r


def test_room_listening_keeps_household_voices_and_drops_strangers():
    owner = np.array([1.0, 0.0])
    known = _room(_VoiceID({"owner": owner}))
    assert known._who(np.array([1.0, 0.05] + [0.0] * 32000)) in ("You", users.get("owner").get("name") or "You")
    stranger = np.array([0.0, 1.0] + [0.0] * 32000)
    assert known._who(stranger) is None                                   # not recorded without guest consent
    assert _room(_VoiceID({"owner": owner}), guests=True)._who(stranger) == "Guest"
    nobody = _VoiceID({})
    nobody.enrolled = False
    assert _room(nobody)._who(stranger) == "Someone"                      # can't tell voices apart: consent covers


def test_voice_id_on_room_audio_never_adapts_the_voiceprint():
    v = _VoiceID({"owner": np.array([1.0, 0.0])})
    before = v.prints["owner"].copy()
    _room(v)._who(np.array([0.99, 0.1] + [0.0] * 32000))
    assert np.array_equal(v.prints["owner"], before)


def test_room_utterances_are_transcribed_into_the_owners_day():
    class STT:
        def transcribe(self, audio):
            return "the boiler is making that noise again"
    r = awareness.RoomListener(listener=None, stt=STT(), voiceid=None, idle=lambda: True)
    r._keep(np.zeros(16000, dtype=np.int16))
    row = awareness.entries()[-1]
    assert row["source"] == "room" and row["who"] == "Someone" and "boiler" in row["text"]


# -- API -----------------------------------------------------------------------------------------
from tests.test_app import client, pair  # noqa: E402,F401


def test_api_timeline_session_delete_keep(client, monkeypatch):
    from core import pcbridge
    h = pair(client)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(awareness, "_host", lambda: "pc")
    client.post("/api/chat", json={"text": "remember the blue folder"}, headers=h)   # conversation gets logged
    day = client.get("/api/awareness", headers=h).json()
    assert day["can_listen"] and day["session"]["state"] == "off"
    assert any("blue folder" in e["text"] for e in day["entries"])
    assert client.post("/api/awareness/session", json={"action": "start"}, headers=h).status_code == 400  # no consent
    on = client.post("/api/awareness/session", json={"action": "start", "consent": True}, headers=h).json()
    assert on["session"]["state"] == "on"
    eid = day["entries"][0]["id"]
    from tools import vault
    monkeypatch.setattr(vault, "remember_note", lambda text, title="": "Saved to the memory vault.")
    assert client.post("/api/awareness/keep", json={"ids": [eid]}, headers=h).json()["ok"]
    gone = client.post("/api/awareness/delete", json={"everything": True}, headers=h).json()
    assert gone["deleted"] >= 1 and client.get("/api/awareness", headers=h).json()["entries"] == []
    assert client.post("/api/awareness/delete", json={}, headers=h).status_code == 400


def test_api_household_members_see_their_own_day_but_not_the_room_controls(client):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    h = pair(client, wife)
    client.post("/api/chat", json={"text": "my own question"}, headers=h)
    day = client.get("/api/awareness", headers=h).json()
    assert not day["can_listen"] and day["session"] is None
    assert [e["who"] for e in day["entries"]][-1] == "Marie" or any(e["who"] == "Marie" for e in day["entries"])
    assert client.post("/api/awareness/session", json={"action": "start", "consent": True}, headers=h).status_code == 403
