"""core/habits: Jarvis learns WHEN the owner does things and proposes a protocol; nothing runs until he approves it.

Rules (owner 2026-11-16): never act on a habit he hasn't approved (the protocol engine's own "approve the X protocol");
after approval it runs on its own and tells him once; "stop doing that" removes it; everything local, no model.
The first test uses his REAL Apex start times (gaming/sessions.json, 2026-10-26..11-16): they are not a habit."""
import json
from datetime import datetime, timedelta

import pytest

from core import habits as H
from core import protocols as P
from core import quick, storage, users

NOW = datetime(2026, 11, 16, 15, 0)        # a Monday


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    import time
    monkeypatch.setattr(P, "_STARTED", time.monotonic() - 10_000)
    monkeypatch.setattr(P, "_process_age", lambda: 10_000.0)
    from core import acquire
    monkeypatch.setattr(acquire, "running_game", lambda: "")
    monkeypatch.setattr(P, "resolve", lambda target: target.title())     # every app is "installed"
    monkeypatch.setattr(P, "_here", lambda: True)
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)              # the PC: it proposes and watches windows


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(kind):
        def run(step, ctx):
            seen.append((kind, dict(step)))
            return P.DONE, f"did {kind}"
        return run
    monkeypatch.setattr(P, "ACTIONS", {k: fake(k) for k in P.ACTIONS})
    return seen


def games(starts: list[str], game: str = "Apex Legends") -> None:
    storage.save(H.GAMES_FILE, [{"game": game, "start": s, "minutes": 60, "clips": []} for s in starts])


def watching(days: list[datetime]) -> None:
    """Jarvis ran on these days (one turn-timing row each, channel phone: a watched day, never an event)."""
    path = storage.DATA / "learning" / "turn-timing-pc.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for d in days:
            f.write(json.dumps({"at": d.replace(hour=12).timestamp(), "channel": "phone", "said": "How are you?"}) + "\n")


def said(lines: list[tuple[datetime, str, str]]) -> None:
    path = storage.DATA / "learning" / "turn-timing-pc.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for at, channel, text in lines:
            f.write(json.dumps({"at": at.timestamp(), "channel": channel, "said": text}) + "\n")


def days_back(n: int) -> list[datetime]:
    return [NOW - timedelta(days=i) for i in range(n)]


def evening_apex(days: int = 8, played: int = 6) -> None:
    """Watched `days` days; on `played` of them he started Apex between 19:50 and 20:15."""
    watching(days_back(days))
    minutes = [55, 5, 10, 50, 0, 15, 58, 2]
    starts = [(NOW - timedelta(days=i + 1)).replace(hour=19 if minutes[i] > 30 else 20, minute=minutes[i]) for i in range(played)]
    games([s.isoformat(timespec="minutes") for s in starts])


# -- measuring -------------------------------------------------------------------------------------------------------
REAL_APEX = ["2026-10-26T18:11", "2026-10-27T19:46", "2026-10-28T21:25", "2026-10-30T21:32", "2026-11-04T20:31",
             "2026-11-07T19:57", "2026-11-10T15:41", "2026-11-11T18:47", "2026-11-14T11:21", "2026-11-14T11:23",
             "2026-11-16T11:36", "2026-11-16T11:49"]


def test_his_real_apex_times_are_not_a_habit():
    games(REAL_APEX)
    watching([datetime(2026, 11, d) for d in (8, 9, 12, 13, 15)])
    found = H.analyse(*H.evidence(NOW), NOW)
    apex = next(c for c in found if c["subject"] == "Apex Legends")
    assert not apex["habit"], "played often, but from 11 AM to 9:30 PM: no time to pin"
    assert apex["event_days"] == 10 and apex["hits"] < H.MIN_HITS
    assert H.tick(NOW.replace(hour=12), deliver=lambda t: pytest.fail(t)) == ""
    assert "Nothing is regular enough" in H.report(NOW)


def test_a_regular_evening_game_is_a_habit():
    evening_apex()
    c = next(c for c in H.analyse(*H.evidence(NOW), NOW) if c["subject"] == "Apex Legends")
    assert c["habit"] and c["label"] == "every day" and c["at"] == "20:00"
    assert (c["hits"], c["watched"]) == (6, 8)


def test_one_busy_day_is_not_a_habit():
    watching(days_back(10))
    said([(NOW.replace(hour=22, minute=m) - timedelta(days=1), "voice", "Open ChatGPT.") for m in (1, 5, 9, 20, 30)])
    assert not any(c["habit"] for c in H.analyse(*H.evidence(NOW), NOW))


def test_too_few_watched_days_is_said_not_proposed():
    evening_apex(days=5, played=5)
    c = next(c for c in H.analyse(*H.evidence(NOW), NOW) if c["subject"] == "Apex Legends")
    assert not c["habit"] and "only been watching" in c["short"]


@pytest.mark.parametrize("line,app", [("Open ChatGPT.", "chatgpt"), ("Hey Jarvis, open Discord", "discord"),
                                      ("Can you open Spotify?", "spotify"), ("Open Discord. Never mind.", ""),
                                      ("Open notepad. Stop.", ""), ("Don't open Steam", ""), ("Open notepad.", ""),
                                      ("What's the news?", ""), ("Open it", "")])
def test_spoken_opens(line, app):
    assert H._spoken_open(line) == app


def test_phone_lines_are_never_evidence():
    watching(days_back(10))
    said([(d.replace(hour=9), "phone", "Open Spotify") for d in days_back(10)])
    events, _ = H.evidence(NOW)
    assert events == [], "the phone channel is mostly live checks and replays"


def test_a_weekend_routine_gets_a_weekend_trigger(calls):
    watching(days_back(28))
    weekend = [d for d in days_back(28) if d.weekday() >= 5]
    said([(d.replace(hour=10, minute=5), "voice", "Open Spotify") for d in weekend])
    c = next(c for c in H.analyse(*H.evidence(NOW), NOW) if c["subject"] == "spotify")
    assert c["habit"] and c["label"] == "on weekends" and c["days"] == [5, 6]
    text = H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    assert "on weekends at 10 AM" in text
    proto = P.find("weekend morning spotify")
    assert proto["triggers"] == [{"type": "time", "at": "10:00", "days": [5, 6]}]
    P.approve("weekend morning spotify")
    saturday = datetime(2026, 11, 21, 10, 2)
    assert [p["name"] for p in P.due(False, saturday)] == ["weekend morning spotify"]
    assert P.due(False, datetime(2026, 11, 23, 10, 2)) == [], "Monday: not a weekend day"
    assert "on weekends at 10 AM" in P.describe(P.find("weekend morning spotify"))


# -- proposing and approving -----------------------------------------------------------------------------------------
def test_a_habit_is_proposed_once_and_never_runs_unapproved(calls):
    evening_apex()
    told = []
    text = H.tick(NOW.replace(hour=12), deliver=told.append)
    assert told == [text]
    assert text.startswith("You usually start Apex Legends around 8 PM (6 of the last 8 days I was watching).")
    assert "run the gaming protocol every day at 8 PM" in text and "approve the evening gaming protocol" in text
    proto = P.find("evening gaming")
    assert proto["steps"] == P.GAMING["steps"], "the game his Gaming protocol opens: that protocol's steps"
    assert proto["learned"]["habit"] == "game:apex legends"
    assert not P.approved(proto)
    assert P.due(False, datetime(2026, 11, 16, 20, 1)) == [], "unapproved: never due"
    assert H.tick(NOW.replace(hour=13), deliver=told.append) == "", "once a day"
    assert H.tick((NOW + timedelta(days=1)).replace(hour=12), deliver=told.append) == "", "one open suggestion at a time"
    assert len(told) == 1


def test_no_suggestions_at_night():
    evening_apex()
    assert H.tick(NOW.replace(hour=23), deliver=lambda t: pytest.fail(t)) == ""


def test_approval_by_voice_then_it_runs_and_tells_him_once(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    assert quick.handle("Approve the evening gaming protocol").startswith("Approved. The evening gaming protocol will now run")
    protos = P.due(False, datetime(2026, 11, 16, 20, 1))
    assert [p["name"] for p in protos] == ["evening gaming"]
    out = []
    P.run_due(users.current(), protos, deliver=out.append)
    assert [k for k, _ in calls] == ["free_up", "close_tab", "set_volume", "open_app", "open_app"]
    assert len(out) == 1 and "for the first time, as you approved" in out[0] and "stop doing that" in out[0]
    P.run_due(users.current(), P.due(False, datetime(2026, 11, 17, 20, 1)), deliver=out.append)
    assert len(out) == 1, "afterwards a run that went fine says nothing"


def test_a_failed_step_is_always_said(calls, monkeypatch):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    P.approve("evening gaming")
    out = []
    P.run_due(users.current(), P.due(False, datetime(2026, 11, 16, 20, 1)), deliver=out.append)
    monkeypatch.setitem(P.ACTIONS, "open_app", lambda step, ctx: (P.FAILED, "Steam is asking you to sign in"))
    P.run_due(users.current(), P.due(False, datetime(2026, 11, 17, 20, 1)), deliver=out.append)
    assert len(out) == 2 and "Steam is asking you to sign in" in out[1]


def test_never_to_an_empty_room(calls, monkeypatch):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    P.approve("evening gaming")
    monkeypatch.setattr(P, "_here", lambda: False)
    ran = []
    monkeypatch.setattr(P, "run_due", lambda uid, protos, deliver=None: ran.append(protos))
    assert P.tick(False, now=datetime(2026, 11, 16, 20, 1)) == []
    assert P.tick(False, now=datetime(2026, 11, 16, 20, 3)) == []
    runs = [r for r in storage.load(P.RUNS, []) if r["name"] == "evening gaming"]
    assert len(runs) == 1 and "weren't at the PC" in runs[0]["stopped"], "said once, not every tick"
    monkeypatch.setattr(P, "_here", lambda: True)
    assert [p["name"] for p in P.tick(False, now=datetime(2026, 11, 16, 20, 5))] == ["evening gaming"], "he sat down in time"
    assert ran


# -- stopping it -----------------------------------------------------------------------------------------------------
def test_stop_doing_that_after_it_ran_removes_it_for_good(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    P.approve("evening gaming")
    P.run_due(users.current(), P.due(False, datetime.now().replace(hour=20, minute=1)), deliver=lambda t: None)
    reply = quick.handle("Stop doing that.")
    assert reply.startswith("Done: I removed the evening gaming protocol") and "The gaming protocol itself is unchanged" in reply
    assert P.find("evening gaming") is None and P.find("gaming") is not None
    assert "game:apex legends" in H.declined()
    assert H.tick((NOW + timedelta(days=5)).replace(hour=12), deliver=lambda t: pytest.fail(t)) == "", "never suggested again"


def test_stop_doing_that_right_after_the_suggestion(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    assert quick.handle("No, don't do that again").startswith("Done: I removed the evening gaming protocol")


def test_stop_doing_that_after_he_said_something_else_is_not_ours(calls):
    """Review 2026-11-16: 'that' meant the habit for 6 hours, so a line for the companion or the music deleted it."""
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    import time
    said([(datetime.fromtimestamp(time.time() + 1), "voice", "Play some music")])     # that turn's row, as main.py writes it
    assert H.handle("Stop doing that.") is None
    assert P.find("evening gaming") is not None and H.declined() == set()


def test_stop_doing_that_long_after_is_not_ours(calls, monkeypatch):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    import time
    later = time.time() + H.STOP_WINDOW_S + 60
    monkeypatch.setattr(H.time, "time", lambda: later)
    assert H.handle("Stop doing that, Jarvis.") is None
    assert P.find("evening gaming") is not None


def test_stop_doing_that_with_nothing_learned_is_not_ours():
    assert H.handle("Stop doing that.") is None


def test_deleting_it_by_name_also_means_never_again(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    assert quick.handle("Delete the evening gaming protocol") == "Deleted the evening gaming protocol."
    assert "game:apex legends" in H.declined()


def test_only_the_owner(monkeypatch):
    monkeypatch.setattr(P, "_is_owner", lambda uid=None: False)
    assert H.handle("Stop doing that.") is None
    assert H.handle("What habits have you noticed?") is None


@pytest.mark.parametrize("line", ["What habits have you noticed?", "Jarvis, have you noticed any routines?",
                                  "What have you learned about my routine?", "Any protocol suggestions?", "What are my habits?"])
def test_he_can_ask_what_was_noticed(line):
    games(REAL_APEX)
    watching([datetime(2026, 11, d) for d in (8, 9, 12, 13, 15)])
    reply = quick.handle(line)
    assert reply.startswith("I've been watching for")
    assert "Apex Legends: 10 days, but you started it anywhere from 11:21 AM to 9:32 PM; no more than 3 of those" in reply


# -- watching apps ---------------------------------------------------------------------------------------------------
def test_observe_records_only_newly_opened_apps():
    t0 = NOW.timestamp()
    first = [{"process": "Discord.exe"}, {"process": "pythonw.exe"}]
    assert H.observe(now=t0, windows=first) == [], "already open when Jarvis started: not opened now"
    boot = first + [{"process": "OneDrive.exe"}]
    assert H.observe(now=t0 + 60, windows=boot) == [], "appeared in Jarvis's first minutes: started with Windows"
    later = boot + [{"process": "Spotify.exe"}, {"process": "notepad.exe"}, {"process": "r5apex.exe"}]
    assert H.observe(now=t0 + H.SETTLE_S + 60, windows=later) == ["spotify"]
    assert H.observe(now=t0 + H.SETTLE_S + 120, windows=later) == []
    rows = H._jsonl(H._app_log_path())
    assert [r["app"] for r in rows] == [None, "spotify"], "one heartbeat for the day, then the app"
    events, watched = H.evidence(NOW + timedelta(minutes=10))
    assert [e["subject"] for e in events] == ["spotify"] and NOW.date() in watched


def test_a_flickering_window_is_logged_once_a_day():
    t0 = NOW.timestamp()
    H.observe(now=t0, windows=[{"process": "Discord.exe"}])
    for i in range(1, 40):
        wins = [{"process": "Discord.exe"}] + ([{"process": "Updater.exe"}] if i % 2 else [])
        H.observe(now=t0 + H.SETTLE_S + i * 60, windows=wins)
    assert [r["app"] for r in H._jsonl(H._app_log_path())] == [None, "updater"]


def test_an_empty_look_is_not_everything_closed():
    t0 = NOW.timestamp()
    wins = [{"process": "Discord.exe"}, {"process": "Spotify.exe"}]
    H.observe(now=t0, windows=wins)
    assert H.observe(now=t0 + H.SETTLE_S + 60, windows=[]) == []
    assert H.observe(now=t0 + H.SETTLE_S + 120, windows=wins) == []


def test_the_laptop_neither_watches_nor_proposes(monkeypatch):
    evening_apex()
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    assert H.tick(NOW.replace(hour=12), deliver=lambda t: pytest.fail(t)) == ""
    assert H.observe(now=NOW.timestamp(), windows=[{"process": "Discord.exe"}]) == []
    assert P.find("evening gaming") is None


def test_a_weekday_routine_is_not_proposed_for_every_day():
    """Review 2026-11-16: every day 20 of 28 also cleared the bar and won, so it would open the app on weekends."""
    watching(days_back(28))
    said([(d.replace(hour=9, minute=2), "voice", "Open Spotify") for d in days_back(28) if d.weekday() < 5])
    c = next(c for c in H.analyse(*H.evidence(NOW), NOW) if c["subject"] == "spotify")
    assert c["habit"] and c["label"] == "on weekdays" and c["days"] == [0, 1, 2, 3, 4]


def test_declined_stays_declined_when_its_days_change(calls):
    H.decline("game:apex legends:every day")              # an older key with the day set
    watching(days_back(28))
    games([(d.replace(hour=20, minute=5)).isoformat(timespec="minutes") for d in days_back(28) if d.weekday() < 5])
    assert H.tick(NOW.replace(hour=12), deliver=lambda t: pytest.fail(t)) == ""


def test_malformed_rows_are_skipped_not_fatal():
    evening_apex()
    rows = storage.load(H.GAMES_FILE, [])
    rows += [{"game": "Apex Legends", "start": "2026-11-10T20:00:00+00:00"}, {"game": "X", "start": None}, "junk", {"start": "x"}]
    storage.save(H.GAMES_FILE, rows)
    path = storage.DATA / "learning" / "turn-timing-pc.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"at": Infinity, "channel": "voice", "said": "Open Spotify"}\n{"at": 1e30, "channel": "voice"}\nnot json\n')
    found = H.analyse(*H.evidence(NOW), NOW)
    assert any(c["subject"] == "Apex Legends" and c["habit"] for c in found)


def test_a_broken_habits_file_never_stops_the_run(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    P.approve("evening gaming")
    storage.save(H.STATE, {"told": "not a list", "declined": []})
    out = []
    assert len(P.run_due(users.current(), P.due(False, datetime(2026, 11, 16, 20, 1)), deliver=out.append)) == 1
    assert len(out) == 1


@pytest.mark.parametrize("line", ["Any suggestions?", "any patterns?", "What patterns?", "Do you have any suggestions?"])
def test_ordinary_follow_ups_are_not_the_habit_report(line):
    assert H.handle(line) is None


# -- review round 2 --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("other", ["what protocols do I have", "blue notebook", "Yes.", "x" * 300])
def test_any_turn_after_the_suggestion_ends_what_that_means(calls, other):
    """Lines answered before core/habits (protocol lines, taught commands, a resumed yes, a long burst) still end it:
    every turn leaves a turn-timing row, whoever answered it."""
    import time
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    said([(datetime.fromtimestamp(time.time() + 1), "voice", other)])
    assert H.handle("Stop doing that.") is None
    assert P.find("evening gaming") is not None and H.declined() == set()


def test_a_held_suggestion_is_not_what_that_means(calls, monkeypatch):
    evening_apex()
    from core import attention
    monkeypatch.setattr(attention, "deliver", lambda text, urgency="", key="", sit=None: "held")
    text = H.tick(NOW.replace(hour=12))
    assert "delete the evening gaming protocol" in text
    assert H.handle("Stop doing that.") is None


def test_a_protocol_stopped_after_it_was_found_due_does_not_run(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    P.approve("evening gaming")
    protos = P.due(False, datetime(2026, 11, 16, 20, 1))
    assert H.handle("Stop doing that.").startswith("Done")
    assert P.run_due(users.current(), protos, deliver=lambda t: None) == [] and calls == []


# -- review round 3 --------------------------------------------------------------------------------------------------
def test_a_turn_that_left_no_timing_row_still_ends_what_that_means(calls):
    """A silent or cut-off reply writes no turn-timing row; the turn's start (Brain._ask -> note_turn) still counts."""
    import time
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    time.sleep(0.01)
    H.note_turn(users.current(), "Mute the music")
    H.note_turn(users.current(), "Stop doing that.")           # the line itself doesn't count against itself
    assert H.handle("Stop doing that.") is None


def test_the_stop_line_itself_is_not_a_later_turn(calls):
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    H.note_turn(users.current(), "[Sent from their Jarvis phone app] Stop doing that.")
    assert H.handle("Stop doing that.").startswith("Done")


def test_a_short_timing_file_keeps_its_first_row(calls):
    import time
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    path = storage.DATA / "learning" / "turn-timing-server.jsonl"
    path.write_text(json.dumps({"at": time.time() + 1, "channel": "phone", "said": "x"}) + "\n", encoding="utf-8")
    assert H.handle("Stop doing that.") is None


def test_a_phone_typed_curly_apostrophe_is_still_the_stop_line(calls):
    """Review round 4: note_turn read "don’t do that again" as another turn, so the phone could never stop it."""
    evening_apex()
    H.tick(NOW.replace(hour=12), deliver=lambda t: None)
    line = "[Sent from their Jarvis phone app] don’t do that again"
    H.note_turn(users.current(), line)
    assert H.handle(line).startswith("Done")
