"""The core scorecard sees his real command types (2026-11-21): on 2026-11-20 it counted 40 turns and 0 commands while his
protocol and stream orders failed (data/jarvis.log 23:42-23:49). Both ways: his orders are commands judged by what was done,
and conversation about the same things stays conversation (a wrong miss is as bad as a hidden one)."""
from datetime import datetime

import pytest

from core import scorecard as S


# -- kinds: his real lines ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said,kind,act", [
    # 2026-11-20 night
    ("Go live.", "stream", "go"), ("Go live. Understood, sir.", "stream", "go"), ("Am I live?", "stream", "status"),
    ("And stream.", "stream", "end?"), ("And the stream.", "stream", "end?"), ("End. Stream.", "stream", "end"),
    ("Hey Jarvis. End. The stream.", "stream", "end"), ("End the stream", "stream", "end"),
    ("Gaming Protocol.", "protocol", "run:gaming"), ("And Streaming Protocol.", "protocol", "run:streaming"),
    ("Hey Jarvis. I'm sorry Seth. Streaming Protocol.", "protocol", "run:streaming"),
    ("Hey Jarvis, start streaming protocol.", "protocol", "run:streaming"),
    ("And streaming protocol. That's what I was telling you. Understood, sir. Yeah, whatever, man. You're not here for shit. "
     "You can shut the fuck up. That's what you can do. All right. Let's see.", "protocol", "run:streaming"),
    ("Stop the gaming protocol.", "protocol", "stop:gaming"),
    # media / volume, reminders, calendar, pairing, on-screen actions (data/jarvis.log, core-turns-server.jsonl)
    ("put the volume up by 15 percent", "media", "volume"), ("Put the volume down by 50%.", "media", "volume"),
    ("volume up", "media", "volume"), ("Mute.", "media", "volume"), ("pause the video", "media", "pause"),
    ("Resume the video.", "media", "play"), ("Play the video.", "media", "play"), ("Next song.", "media", "skip"),
    ("Turn off the music.", "media", "pause"),
    ("Set a timer for 5 minutes.", "reminder", "set"), ("Remind me to call the bank at 3 pm.", "reminder", "set"),
    ("start a timer", "reminder", "set"), ("Cancel the pasta timer.", "reminder", "cancel"),
    ("What's on my calendar today?", "calendar", "read"), ("Put Jim on my calendar tomorrow at six pm", "calendar", "write"),
    ("pair my phone.", "pair", ""), ("Scroll all the way up.", "ui", "scroll"),
    ("Click on autonomous blender learning in ChatGPT.", "ui", "click"),
    ("On ChatGPT, I want you to click on Autonomous Blender Learning.", "ui", "click"),
    ("Hey Jarvis, type how you doing in Notepad.", "ui", "type"),
])
def test_his_commands_have_a_kind(said, kind, act):
    assert S.kind(said) == kind, said
    assert S.act(said) == act, said


@pytest.mark.parametrize("said", [
    "Am I alive?", "End it.", "The stream.", "Streaming.", "Protocol.", "Yes, sir.", "Yeah, it's good.",
    "I want to show you live.", "The universe will be live.", "I think the stream was good.", "What is a network protocol?",
    "What does the streaming protocol do?", "Streaming protocol. No, wait.", "I ran the streaming protocol yesterday.",
    "Okay, so the volume is still at 100.", "you you have not decreased the volume by 50 percent",
    "play rock, paper, scissors.", "When I say practice mode, remind me to stretch, okay?", "repair my phone.",
    "I'm going to go in and press save here.", "On them, it's staying here. You say, it says save. I'm going to click on save.",
    "I'd um birthday on my calendar today from nine fifty five to ten fifty five even", "Turn off the lights.",
    "Stop.", "Go to sleep.", "Show me the weather in Paris",
    # review 2026-11-21: plausible talk that the first patterns took for commands
    "Remind me why we picked that.", "Remind me what I said about the stream.", "Remind me, what's the plan?",
    "Type 2 diabetes runs in my family on my dad's side.", "Type of music in my playlist is mostly rock.",
    "Next song is good.", "Play the video game with me.", "Stop the video from lagging.", "Put the volume on the roadmap.",
    "Add a button to the calendar page.", "Add a new tile to the calendar.", "Move the meeting to the calendar tab.",
    "What's wrong with my calendar?", "Check the calendar code.",
])
def test_conversation_stays_conversation(said):
    assert S.kind(said) not in ("protocol", "stream", "media", "reminder", "calendar", "pair", "ui"), said


def test_the_old_kinds_are_unchanged():
    assert S.kind("Open Spotify.") == "open" and S.kind("Turn on satellites.") == "globe"
    assert S.kind("Open Paint. Never mind.") == "cancel" and S.kind("Minimize Notepad.") == "minimize"


# -- outcomes: by the action asked for ---------------------------------------------------------------------------------
@pytest.mark.parametrize("said,reply,out", [
    ("Go live.", "Right away, sir: opening Livecaptions. Opened livecaptions.", "talk"),     # 11-20 23:42: the wrong thing
    ("Go live.", "You're live on Twitch.", "done"), ("Am I live?", "You're live on Twitch, 1 min in.", "done"),
    ("Go live.", "Nothing went live: Twitch has no stream key in OBS's Stream settings.", "failed"),
    ("And stream.", "You're live on Twitch and streaming now, sir.", "talk"),             # he meant END
    ("Hey Jarvis. End. The stream.", "Very good, sir.", "talk"), ("End the stream", "Stream ended on Twitch.", "done"),
    ("End the stream", "You're not live anywhere, so there was nothing to end.", "done"),
    ("And Streaming Protocol.", "Got it, sir.", "talk"),
    ("Hey Jarvis. I'm sorry Seth. Streaming Protocol.", "Understood, sir: I'm here for the streaming protocol, ready to help.", "talk"),
    ("Gaming Protocol.", "Gaming protocol: 4 of 5 steps done. Done: nothing heavy was running in the background", "done"),
    ("Stop the gaming protocol.", "Putting everything back: background work is back to its usual priority.", "done"),
    ("Pause the video.", "The video is now playing.", "talk"), ("pause the video", "The video has stopped.", "done"),
    ("Resume the video.", "The video is still paused.", "failed"), ("Play the video.", "The video is now playing.", "done"),
    ("put the volume up by 15 percent", "I've increased the volume by 15%.", "done"),
    ("Put the volume up to 50%.", "Volume is now at 50%.", "done"), ("volume up", "Understood, sir.", "talk"),
    ("Set a timer for 5 minutes.", "Five minutes on the timer. I'll tell you when it's done.", "done"),
    ("Remind me to stretch at 9.", "Reminder set for Saturday 21 November at 09:00.", "done"),
    ("What's on my calendar?", "Nothing on your calendar today.", "done"),
    ("What's on my calendar today?", "Your calendar is clear for today, sir.", "done"),
    ("Put Jim on my calendar tomorrow at six pm", "Added to your calendar: Jim at six pm, Saturday.", "done"),
    ("Put Jim on my calendar tomorrow at six pm", "Got it, sir.", "talk"),
    ("pair my phone.", "Your pairing code is 9, 1, 6, 6, 2, 4.", "done"),
    ("Scroll all the way up.", "One moment, sir, putting that together now. Scrolled up.", "done"),
    ("Click on autonomous blender learning in ChatGPT.", "Opened chatgpt.", "talk"),
    ("On ChatGPT, I want you to click on Autonomous Blender Learning.",
     "Focused a visible ChatGPT window. Clicked Autonomous Blender Learning in ChatGPT.", "done"),
    ("I need you to type in Claude message box.", "I pasted the text into claude, but reading the window back I don't see it. "
                                                  "I could not finish the step.", "failed"),
])
def test_outcome_is_the_action_asked_for(said, reply, out):
    assert S.outcome(S.kind(said), reply, said) == out, (said, reply)


# -- a protocol order is first-try only when it really started ---------------------------------------------------------
def _row(clock, said, reply):
    ts = datetime(2026, 11, 20, *map(int, clock.split(":"))).timestamp()
    k = S.kind(said)
    row = {"ts": ts, "channel": "voice", "said": said, "kind": k, "outcome": S.outcome(k, reply, said), "mishear": S.misheard(said),
           "reply_head": reply[:60]}
    if S.act(said, k):
        row["act"] = S.act(said, k)
    return row


def _at(clock):
    return datetime(2026, 11, 20, *map(int, clock.split(":"))).timestamp()


NIGHT = [  # the live 2026-11-20 rows (core-turns-pc.jsonl: his words as kept, turn end), and its two recorded runs
    ("23:45:55", "I'm sorry Seth. Streaming Protocol.", "Understood, sir: I'm here for the streaming protocol, ready to help."),
    ("23:46:25", "start streaming protocol.", ""),                    # cut before a word; the run began at 23:46:04
    ("23:46:26", "Streaming.", "Understood, sir: I'm here and ready to help with whatever you need."),
    ("23:46:29", "Protocol.", "Understood, sir: protocol engaged. What do you need me to run?"),
    ("23:46:36", "Gaming Protocol.", "Gaming protocol: 4 of 5 steps done. Done: nothing heavy was running in the background"),
    ("23:48:21", "And Streaming Protocol.", "Got it, sir."),
]
RUNS = ([("streaming", _at("23:46:04")), ("gaming", _at("23:46:36"))], 0.0)


def test_the_nov_20_protocol_orders_are_judged_by_the_runs():
    judged = [r for r in S.judge([_row(*x) for x in NIGHT], runs=RUNS) if r["first_try"] is not None]
    assert [(r["said"], r["first_try"]) for r in judged] == [
        ("I'm sorry Seth. Streaming Protocol.", False),   # the run that began 9 s later was the NEXT order's
        ("start streaming protocol.", True),              # silent, but it ran
        ("Gaming Protocol.", True),
        ("And Streaming Protocol.", False),               # "Got it, sir." and nothing ran
    ]


def test_got_it_is_never_done_and_one_run_answers_one_order():
    rows = [_row("23:48:21", "And Streaming Protocol.", "Got it, sir."), _row("23:48:40", "Streaming protocol.", "Got it, sir.")]
    runs = ([("streaming", _at("23:48:30"))], 0.0)
    assert [r["first_try"] for r in S.judge(rows, runs=runs)] == [False, True]
    assert [r["first_try"] for r in S.judge(rows, runs=([], 0.0))] == [False, False]
    late = [_row("23:48:21", "Gaming protocol.", "Got it, sir.")]
    assert S.judge(late, runs=RUNS)[0]["first_try"] is False           # the 23:46:36 gaming run was long over
    other = [_row("23:46:40", "Streaming protocol.", "Got it, sir.")]
    assert S.judge(other, runs=([("gaming", _at("23:46:36"))], 0.0))[0]["first_try"] is False   # ANOTHER protocol ran


def test_an_order_older_than_the_kept_runs_is_left_to_the_reply():
    rows = [_row("23:46:36", "Gaming Protocol.", "Gaming protocol: 4 of 5 steps done.")]
    assert S.judge(rows, runs=([], _at("23:50:00")))[0]["first_try"] is True


def test_the_nov_20_stream_lines_count_as_misses_and_repeats():
    rows = [_row("23:42:50", "Go live.", "You're live on Twitch."), _row("23:43:22", "Am I live?", "You're live on Twitch, 1 min in."),
            _row("23:43:27", "And stream.", "You're live on Twitch and streaming now, sir."),
            _row("23:43:33", "And stream.", "As I said: You're live on Twitch and streaming now, sir.")]
    c = S.card(rows)
    assert c["commands"] == 4 and c["first_try"] == [2, 4] and c["by_kind"] == {"stream": [2, 4]}
    assert c["repeats"] == 1 and c["mishears"] == 2                    # "And stream." is Whisper's "End stream."


def test_volume_up_twice_is_more_not_a_repeat():
    rows = [_row("21:00:00", "Volume up.", "Volume is now at 60%."), _row("21:00:10", "Volume up.", "Volume is now at 70%.")]
    assert S.card(rows)["first_try"] == [2, 2]


def test_rows_kept_before_the_kinds_existed_are_read_with_them(monkeypatch, tmp_path):
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    (tmp_path / "learning").mkdir()
    import json
    old = [{"ts": _at("23:46:36"), "channel": "voice", "said": "Gaming Protocol.", "kind": "other", "outcome": "done",
            "mishear": False, "reply_head": "Gaming protocol: 4 of 5 steps done. Done: nothing heavy"},
           {"ts": _at("23:48:21"), "channel": "voice", "said": "And Streaming Protocol.", "kind": "other", "outcome": "talk",
            "mishear": False, "reply_head": "Got it, sir."},
           {"ts": _at("23:50:00"), "channel": "voice", "said": "How are you doing?", "kind": "other", "outcome": "talk",
            "mishear": False, "reply_head": "I'm fine, sir."}]
    (tmp_path / "learning" / "core-turns-pc.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old), encoding="utf-8")
    rows = S.turns(("pc",))
    assert [(r["kind"], r.get("act"), r["outcome"]) for r in rows] == [
        ("protocol", "run:gaming", "done"), ("protocol", "run:streaming", "talk"), ("other", None, "talk")]
    assert (tmp_path / "learning" / "core-turns-pc.jsonl").read_text(encoding="utf-8").count('"kind": "other"') == 3


# -- review 2026-11-21 ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said,reply,out", [
    ("Play some music.", "Nothing is playing right now, sir.", "talk"), ("Resume the video.", "The video isn't playing.", "talk"),
    ("What's on my calendar?", "You have to connect Google Calendar first.", "talk"),
    ("pair my phone.", "Your phone is not paired yet.", "talk"),
    ("Set a timer for 5 minutes.", "Cancelled the pasta timer.", "talk"),
    ("Cancel the pasta timer.", "Five minutes on the timer. I'll tell you when it's done.", "talk"),
    ("Cancel the pasta timer.", "Cancelled the pasta timer.", "done"),
    ("Turn off the music.", "Turned off the music.", "done"),
    ("Gaming Protocol.", "Gaming protocol: 4 of 5 steps done. Failed: I couldn't open Discord.", "done"),   # it ran
    ("Gaming Protocol.", "Gaming protocol: 0 of 5 steps done. Failed: I couldn't open Discord.", "failed"),
])
def test_not_done_answers_that_hold_a_done_word(said, reply, out):
    assert S.outcome(S.kind(said), reply, said) == out, (said, reply)


@pytest.mark.parametrize("first,second", [
    ("Pause the video.", "Play the video."), ("Start the gaming protocol.", "Stop the gaming protocol."),
    ("Set a timer for five minutes.", "Set a timer for ten minutes."), ("Click on Save.", "Click on Send."),
    ("Remind me to call mom.", "Remind me to call dad."), ("Scroll down.", "Scroll down."),
])
def test_a_different_ask_is_not_a_repeat(first, second):
    rows = [_row("21:00:00", first, "Understood, sir."), _row("21:00:20", second, "Understood, sir.")]
    assert not S.judge(rows, runs=([], 0.0))[0]["repeated"], (first, second)


def test_the_same_ask_again_still_is_a_repeat():
    rows = [_row("21:00:00", "Pause the video.", "The video is now playing."), _row("21:00:20", "Pause the video.", "Paused.")]
    assert S.judge(rows)[0]["repeated"] is True


def test_the_order_that_started_the_run_gets_it_not_the_ack_before():
    rows = [_row("23:46:36", "Gaming protocol.", "Understood, sir."), _row("23:46:40", "Gaming protocol.", "")]
    runs = ([("gaming", _at("23:46:38"))], 0.0)
    assert [r["first_try"] for r in S.judge(rows, runs=runs)] == [False, True]


def test_protocols_are_read_as_his_whoever_asks(monkeypatch, tmp_path):
    import json
    from core import storage, users
    monkeypatch.setattr(storage, "DATA", tmp_path)
    (tmp_path / "learning").mkdir()
    row = {"ts": _at("23:48:21"), "channel": "voice", "said": "Gaming Protocol.", "kind": "other", "outcome": "talk",
           "mishear": False, "reply_head": "Got it, sir."}
    bad = {"ts": _at("23:49:00"), "channel": "voice", "said": 5, "kind": "other"}
    (tmp_path / "learning" / "core-turns-pc.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(bad) + "\n[]\n",
                                                               encoding="utf-8")
    owner = next(u for u, d in users.all_users().items() if d.get("role") == "owner")
    other = next((u for u, d in users.all_users().items() if d.get("role") != "owner"), "guest")
    monkeypatch.setattr(users._local, "uid", other, raising=False)
    seen = []
    real = S.kind
    monkeypatch.setattr(S, "kind", lambda said: (seen.append(users.current()), real(said))[1])
    rows = S.turns(("pc",))
    assert seen and set(seen) == {owner} and users.current() == other
    assert [r["kind"] for r in rows] == ["protocol", "other"]          # the odd row is kept as it was, the card still reads


def test_owner_runs_come_from_his_protocol_runs(monkeypatch, tmp_path):
    import json
    from core import protocols, storage, users
    monkeypatch.setattr(storage, "DATA", tmp_path)
    owner = next(u for u, d in users.all_users().items() if d.get("role") == "owner")
    (tmp_path / "users" / owner).mkdir(parents=True)
    (tmp_path / "users" / owner / protocols.RUNS).write_text(json.dumps([{"name": "gaming", "started": "2026-11-20T23:46:36"}]),
                                                             encoding="utf-8")
    runs, known = S._owner_runs()
    assert runs == [("gaming", _at("23:46:36"))] and known == 0.0
