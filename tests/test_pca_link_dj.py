"""Jarvis runs the workout music through Jarvis Link (tools/pca_music's Link section + tools/pca_link + the page/voice wiring).

The phone is faked by Phone: it takes the queued commands from the real tools/pca_link queue and answers each one the way
it is told to (confirm, fail, or never answer), and reports what is playing. Sandboxed storage (tests/conftest.py), a pinned
clock, the music search replaced by a recorder: no phone, no network, no model, no speaker."""
import base64
import hashlib
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import coachintent, users
from core import pca_music as rules
from tools import pca, pca_link, pca_music, trainer

HIS = "Dababy baby chief sleepy hollow post Malone and more idc I like good music not country tho lol"
MON = datetime(2026, 11, 23, 7, 0)
ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


def vid(q: str, i: int) -> str:
    return hashlib.md5(f"{q}|{i}".encode()).hexdigest()[:11]


def results(q: str, n: int = 6) -> list[dict]:
    if "speech" in q:
        return [{"id": vid(q, i), "title": f"Discipline motivational speech {i}", "channel": "Speeches", "duration_s": 300 + 200 * i} for i in range(n)]
    if "cadence" in q:
        return [{"id": vid(q, i), "title": f"Army running cadence {i}", "channel": "Cadence", "duration_s": 240 + 300 * i} for i in range(n)]
    return [{"id": vid(q, i), "title": f"{q} track {i}", "channel": "Chan", "duration_s": 180 + 10 * i} for i in range(n)]


def b64(v: str) -> str:
    return base64.urlsafe_b64encode(v.encode()).decode().rstrip("=")


class Phone:
    """The Jarvis Link app, faked: answers commands as `mode` says and remembers what it got."""

    def __init__(self, box):
        self.box, self.mode, self.got, self.track = box, "confirm", [], None

    def report(self, title=None, artist="", video="", playing=True, duration_ms=200000, position_ms=30000, media_id=None):
        st = {"playing": playing, "session": True, "package": "com.metrolist.music", "duration_ms": duration_ms,
              "position_ms": position_ms}
        if title:
            st.update(title=title, artist=artist, media_id=media_id if media_id is not None else (f"auto:queue:3:{b64(video)}" if video else ""))
        self.track = st
        pca_link.report_state(st)

    def poll(self):
        for c in pca_link.take()["commands"]:
            self.got.append((c["name"], c["args"]))
            if self.mode == "timeout":
                continue
            ok, state = self.mode == "confirm", None
            if ok and c["name"] == "play_id":
                a = c["args"]
                state = {"playing": True, "session": True, "title": a["title"], "artist": a["artist"],
                         "media_id": f"auto:queue:1:{b64(a['video_id'])}", "duration_ms": 200000, "position_ms": 0}
                self.track = state
            elif ok and c["name"] in ("pause", "play") and self.track:
                state = dict(self.track, playing=c["name"] == "play")
            pca_link.report_result({"id": c["id"], "ok": ok, "detail": "Playing (media id)." if ok else "Metrolist did not start the song.",
                                    "state": state})

    def names(self):
        return [n for n, _ in self.got]


@pytest.fixture
def box(monkeypatch):
    b = {"now": MON, "searched": [], "waits": []}
    monkeypatch.setattr(trainer, "clock", lambda: b["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)
    monkeypatch.setattr(trainer, "SAY", lambda seconds, line, uid: None)
    monkeypatch.setattr(pca, "_open_link", lambda url: None)
    monkeypatch.setattr(pca_link, "clock", lambda: b["now"].timestamp())
    monkeypatch.setattr(pca_link, "machine", lambda: "server")
    monkeypatch.setattr(pca_music, "_backend", lambda q, n: b["searched"].append(q) or results(q))
    for qid, value in {**ANSWERS, "music_prefs": HIS, "music_service": "metrolist"}.items():
        assert pca.page_action({"action": "answer", "id": qid, "value": value}).startswith("Saved"), qid
    assert pca.audio_text({"service": "metrolist"}) == "Music preferences saved."
    assert "starts today" in pca.page_action({"action": "begin"})
    return b


def _pair_link(uid="owner"):
    entry = {"id": "lnk001", "token_hash": "x" * 64, "name": pca_link.DEVICE, "added": "2026-11-20T07:00", "push": None}
    users.mutate_devices(uid, lambda devices: devices + [entry])


@pytest.fixture
def phone(box, monkeypatch):
    _pair_link()
    p = Phone(box)

    def wait_result(cid, timeout=6):
        box["waits"].append(timeout)
        p.poll()
        return pca_link.result(cid)
    monkeypatch.setattr(pca_link, "wait_result", wait_result)
    p.report()                                                            # it checked in: reachable
    return p


def music():
    return [e for e in trainer.events() if e["type"] == "music"]


def autos():
    return [e for e in music() if e["act"] == "auto"]


# ---- playing a pick ------------------------------------------------------------------------------------------------------
def test_voice_pick_plays_on_the_phone_and_says_so(phone, box):
    out = pca_music.next_song("lift")
    assert out["via"] == "link" and out["played"] is True and "playing on your phone" in out["message"]
    assert phone.got[0] == ("play_id", {"video_id": out["song"]["id"], "title": out["song"]["title"],
                                         "artist": out["song"]["artist"] or out["song"]["channel"]})
    assert box["waits"] == [pca_music.LINK_WAIT] and pca_music.LINK_WAIT == 12
    assert pca_music.music_state()["link"]["playing"]["mine"] is True


@pytest.mark.parametrize("mode,words", [("fail", "could not start it"), ("timeout", "did not confirm within 12 seconds")])
def test_voice_pick_falls_back_to_the_tap_link(phone, mode, words):
    phone.mode = mode
    out = pca_music.next_song("lift")
    assert out["ok"] and out["via"] == "tap" and out["played"] is False and words in out["message"]
    assert "Tap Play on the Captain America page" in out["message"] and out["song"]["link"]


def test_no_link_or_a_silent_phone_is_todays_behaviour(box, monkeypatch):
    out = pca_music.next_song("lift")                                     # never paired: exactly as before
    assert "via" not in out and pca_link.take()["commands"] == []
    _pair_link()
    Phone(box).report()
    box["now"] += timedelta(seconds=91)                                   # not heard from for 91 s: not reachable
    assert not pca_link.reachable()
    out = pca_music.next_song("lift", "next")
    assert out["via"] == "tap" and not any(c["name"] == "play_id" for c in pca_link.take()["commands"])
    box["now"] -= timedelta(seconds=2)
    assert pca_link.reachable()                                           # 89 s: still reachable


def test_the_page_never_waits_and_polls_the_outcome(phone, box):
    out = pca.page_action({"action": "music_next", "phase": "lift"})
    assert out.endswith("Sending it to your phone.") and box["waits"] == []
    pend = pca.page_action({"action": "music_state"})["dj"]["link"]["pending"]
    assert pend["waiting"] and not pend["done"] and pend["song_id"] == pca_music.music_state()["now"]["id"]
    phone.poll()                                                          # the phone answers on its next poll
    pend = pca.page_action({"action": "music_state"})["dj"]["link"]["pending"]
    assert pend["done"] and pend["ok"] and not pend["waiting"]
    phone.mode = "timeout"
    pca.page_action({"action": "music_next", "phase": "lift", "reason": "next"})
    phone.poll()
    box["now"] += timedelta(seconds=14)
    phone.report(title="old song")                                        # still in contact, never answered the play
    pend = pca.page_action({"action": "music_state"})["dj"]["link"]["pending"]
    assert pend["done"] and not pend["ok"] and "did not confirm" in pend["detail"]


def test_skip_by_link_records_the_skip_and_plays_jarviss_pick_by_id(phone):
    first = pca_music.next_song("lift")
    up = pca_music.music_state()["upnext"]["id"]
    out = pca.page_action({"action": "music_skip"})
    phone.poll()
    assert "Sending it to your phone" in out
    assert [e["act"] for e in music()][-2:] == ["skip", "play"] and music()[-2]["id"] == first["song"]["id"]
    assert phone.got[-1] == ("play_id", {"video_id": up, "title": pca_music.music_state()["now"]["title"],
                                          "artist": pca_music.music_state()["now"]["artist"] or pca_music.music_state()["now"]["channel"]})
    assert "next" not in phone.names()                                    # never Metrolist's own "next"


def test_like_is_forwarded_and_reported_honestly(phone):
    pca_music.next_song("lift")
    assert pca.music_like() .endswith("Liked in Metrolist too.") and phone.got[-1] == ("like", {})
    pca.music_like()
    assert phone.names().count("like") == 1                               # a second like changes nothing
    pca_music.feedback("unlike")
    phone.mode = "timeout"
    assert pca.music_like().endswith("Your phone did not confirm the like in Metrolist.")
    assert pca.page_action({"action": "music_unlike"}) == "Okay, not liked."
    assert pca.page_action({"action": "music_like"}).endswith("Liking it in Metrolist too.")


def test_pause_resume_and_what_song(phone):
    pca_music.next_song("lift")
    assert pca.music_pause() == "Paused." and phone.got[-1] == ("pause", {})
    assert pca.music_resume() == "Playing again." and phone.got[-1] == ("play", {})
    now = pca_music.music_state()["now"]
    assert pca.music_what() == f"This is {now['title']} by {now['artist'] or now['channel']}. I picked it."
    phone.report(title="Rich Flex", artist="Drake", video="RichFlex001")
    assert pca.music_what() == "This is Rich Flex by Drake. It's from Metrolist's own queue."
    assert pca.page_action({"action": "music_pause"}) == "Pausing the music."
    phone.mode = "timeout"
    assert "did not confirm" in pca.music_pause()


def test_pause_and_what_song_without_a_phone(box):
    assert pca.music_pause() == pca_music._NOT_PAIRED and "not set up" in pca.music_what()
    _pair_link()
    assert "can't reach your phone" in pca.music_pause() and "can't see your phone" in pca.music_what()
    pca.audio_text({"service": "metrolist", "dj": False})
    assert pca.music_pause() == pca_music.OFF


# ---- Metrolist's own queue -------------------------------------------------------------------------------------------------
def test_a_queue_track_is_labelled_and_like_skip_apply_to_it(phone):
    pca_music.next_song("lift")
    phone.report(title="Rich Flex", artist="Drake, 21 Savage", video="RichFlex001")
    pl = pca_music.music_state()["link"]["playing"]
    assert pl["source"] == "metrolist_queue" and not pl["mine"] and pl["title"] == "Rich Flex"
    assert pca.page_action({"action": "music_like"}).startswith("Liked Rich Flex by Drake, 21 Savage from Metrolist's queue.")
    ev = music()[-1]
    assert (ev["act"], ev["source"], ev["artist"], ev["id"]) == ("like", "metrolist_queue", "Drake", "RichFlex001")
    phone.poll()
    assert phone.got[-1] == ("like", {})                                  # liked in Metrolist too
    assert pca.page_action({"action": "music_like"}).startswith("You already liked")
    phone.report(title="Jimmy Cooks", artist="Drake", video="JimmyCooks1")
    assert pca_music.learned_artists(music(), pca_music.taste_now()) == []    # one like: not yet
    pca.music_like()
    assert pca_music.music_state()["learned"] == ["Drake"]
    t, events, learned = pca_music._with_learned(pca_music.taste_now(), music())
    w = rules.artist_weights("lift", events, t)
    assert learned == ["Drake"] and w["DaBaby"] == 1.0
    assert abs(w["Drake"] - 1.3 ** -4 * 1.3 ** 2) < 1e-9                  # starts at 0.35 of a listed artist; his 2 likes x1.3 each
    before = len(phone.got)
    phone.report(title="Search & Rescue", artist="Drake", video="SearchResc1")
    out = pca.music_skip()                                                # voice: skips THAT track, plays Jarvis's next pick
    assert out.startswith("Skipped Search & Rescue by Drake from Metrolist's queue.") and "playing on your phone" in out
    skip = [e for e in music() if e["act"] == "skip"][-1]
    assert skip["source"] == "metrolist_queue" and skip["artist"] == "Drake"
    assert [n for n, _ in phone.got[before:]] == ["play_id"]
    assert pca_music.learned_artists(music(), pca_music.taste_now()) == []    # the skip counts against Drake


def test_jarviss_own_song_is_rated_when_the_page_names_it(phone):
    out = pca_music.next_song("lift")
    phone.report(title="Rich Flex", artist="Drake", video="RichFlex001")
    assert pca.page_action({"action": "music_like", "id": out["song"]["id"]}).startswith("Liked.")
    assert music()[-1]["id"] == out["song"]["id"] and "like" not in phone.names()       # Metrolist plays something else


def test_a_learned_artist_is_picked_with_its_own_why(phone, box, monkeypatch):
    for n, title in enumerate(("A", "B")):
        phone.report(title=f"Rod song {title}", artist="Rod Wave", video=f"RodWave00{n}x")
        pca_music.feedback("like")
    t, events, learned = pca_music._with_learned(pca_music.taste_now(), music())
    assert learned == ["Rod Wave"]
    pool = {q: results(q) for q in rules.queries("artist", "lift", t, "Rod Wave")}
    for seed in range(200):
        song = rules.choose("lift", t, events, pool, random.Random(seed))
        if song and song["artist"] == "Rod Wave":
            assert pca_music._learned_why(song, learned)["why"] == "Because you liked Rod Wave in Metrolist."
            break
    else:
        pytest.fail("a learned artist is never picked")


# ---- the auto-DJ -----------------------------------------------------------------------------------------------------------
@pytest.fixture
def session(phone, box, monkeypatch):
    ph = {"phase": "lift"}
    monkeypatch.setattr(pca, "audio_phase", lambda day: ph["phase"])
    out = pca.page_action({"action": "start"})
    assert trainer.view()["session"]["open"], out
    return ph


def test_auto_dj_starts_music_when_the_workout_starts_and_on_each_phase_change(session, phone, box):
    r = pca_music.tick()
    assert r["action"] == "phase" and "The workout started" in r["reason"]
    phone.poll()
    assert phone.got[-1][0] == "play_id" and pca_music.music_state()["now"]["phase"] == "lift"
    assert autos()[-1]["what"] == "phase" and autos()[-1]["reason"] == r["reason"]
    box["now"] += timedelta(seconds=5)
    assert pca_music.tick()["action"] == "none"
    session["phase"] = "hard"
    box["now"] += timedelta(seconds=5)
    assert pca_music.tick()["action"] == "wait"                          # one automatic change per 20 s
    box["now"] += timedelta(seconds=11)
    r = pca_music.tick()
    assert r["action"] == "phase" and "from the lifting to the hard run" in r["reason"]
    assert pca_music.music_state()["now"]["phase"] == "hard"


def test_auto_dj_keeps_an_acceptable_queue_track_and_replaces_a_wrong_one(session, phone, box):
    pca_music.tick()
    phone.poll()
    box["now"] += timedelta(seconds=30)
    phone.report(title="Rich Flex", artist="Drake", video="RichFlex001", duration_ms=239000)
    r = pca_music.tick()
    assert r["action"] == "keep" and autos()[-1]["what"] == "keep" and "so it stays" in r["reason"]
    n = len(phone.got)
    assert pca_music.tick()["action"] == "none" and len(phone.got) == n   # the same track is judged once
    phone.report(title="Country Roads", artist="John Denver", video="CountryRoad", duration_ms=190000)
    r = pca_music.tick()
    assert r["action"] == "replace" and "avoided: country" in r["reason"]
    phone.poll()
    assert phone.got[-1][0] == "play_id"
    session["phase"] = "hard"
    box["now"] += timedelta(seconds=30)
    pca_music.tick()
    phone.poll()
    box["now"] += timedelta(seconds=30)
    phone.report(title="Discipline - Motivational Speech", artist="Speeches", video="Discipline1", duration_ms=600000)
    r = pca_music.tick()
    assert r["action"] == "replace" and "a speech during the hard run" in r["reason"]
    session["phase"] = "cooldown"
    box["now"] += timedelta(seconds=30)
    pca_music.tick()
    phone.poll()
    box["now"] += timedelta(seconds=30)
    phone.report(title="Gym Hype Workout", artist="Chief Keef", video="GymHype0001")
    assert pca_music.tick()["reason"].endswith("hype music during the cool-down, so I replaced it.")


def test_auto_dj_unknown_length_is_fine(session, phone, box):
    pca_music.tick()
    phone.poll()
    box["now"] += timedelta(seconds=30)
    phone.report(title="Suge", artist="DaBaby", video="Suge0000001", duration_ms=None)
    assert pca_music.tick()["action"] == "keep"


def test_auto_dj_never_without_a_session_and_never_stops_by_default(phone, box, monkeypatch):
    monkeypatch.setattr(pca, "audio_phase", lambda day: "lift")
    phone.report(title="Country Roads", artist="John Denver", video="CountryRoad")
    assert pca_music.tick()["action"] == "none" and phone.names() == [] and pca_link.take()["commands"] == []
    pca.page_action({"action": "start"})
    pca_music.tick()
    phone.poll()
    pca.page_action({"action": "end"})
    assert not trainer.view()["session"]["open"]
    box["now"] += timedelta(seconds=30)
    n = len(phone.got)
    assert pca_music.tick()["action"] == "none"
    phone.poll()
    assert len(phone.got) == n                                            # his music keeps playing


def test_auto_dj_stops_at_the_end_only_when_he_asked(phone, box, monkeypatch):
    monkeypatch.setattr(pca, "audio_phase", lambda day: "lift")
    assert pca.page_action({"action": "music_link", "stop_at_end": True}) == "Jarvis stops the music when the workout ends."
    pca.page_action({"action": "start"})
    pca_music.tick()
    phone.poll()
    pca.page_action({"action": "end"})
    box["now"] += timedelta(seconds=30)
    assert pca_music.tick()["action"] == "stop"
    phone.poll()
    assert phone.got[-1] == ("pause", {}) and autos()[-1]["what"] == "stop"
    assert pca_music.tick()["action"] == "none"                          # once


def test_auto_switch_off_turns_the_auto_dj_off_and_persists(session, phone, box):
    assert pca_music.music_state()["link"]["auto_dj"] is True              # default ON once paired
    assert pca.page_action({"action": "music_link", "auto_dj": False}).startswith("Auto is off")
    assert pca.page_data()["audio"]["dj"]["link"]["auto_dj"] is False
    assert pca.page_data()["audio"]["prefs"]["service"] == "metrolist"     # the other prefs are kept
    assert pca_music.tick()["action"] == "none" and phone.names() == []
    assert pca.page_action({"action": "music_link", "auto_dj": "yes"}).startswith("Send auto_dj")


def test_auto_dj_default_is_off_without_a_paired_link(box):
    assert pca_music.music_state()["link"]["auto_dj"] is False and pca_music.tick()["action"] == "none"


def test_tick_soon_runs_once_at_a_time_and_throttles(phone, monkeypatch):
    import threading
    ran, gate = [], threading.Event()
    monkeypatch.setattr(pca_music, "tick", lambda now=None: ran.append(users.current()) or gate.wait(5))
    monkeypatch.setattr(pca_music, "_tick_last", {})
    assert pca_music.tick_soon("owner") is True
    assert pca_music.tick_soon("owner") is False                           # throttled
    gate.set()


# ---- keeping the phone listening -----------------------------------------------------------------------------------------
def test_idle_hint_is_45_s_and_activity_marks_the_link(phone, box):
    assert pca_link.IDLE_SLEEP == 45
    pca_link.active(0)
    assert pca_link.take()["sleep"] == 45
    pca.page_data()                                                       # the page with the picker on: 10 minutes
    q = pca_link._own_queue()
    assert abs(q["active_until"] - (box["now"].timestamp() + 600)) < 1 and pca_link.take()["sleep"] == 0
    box["now"] += timedelta(seconds=601)
    assert pca_link.take()["sleep"] == 45
    pca_music.next_song("lift")                                           # any music command: 10 minutes
    assert pca_link.is_active()
    box["now"] += timedelta(seconds=601)
    pca.page_action({"action": "start"})                                  # an open workout: 5 min after its last event
    box["now"] += timedelta(seconds=200)
    pca_link.active(0)
    pca_music._keep_for_session(None, pca_music.fitness._data())
    assert abs(pca_link._own_queue()["active_until"] - (box["now"].timestamp() + 100)) < 2


def test_no_queue_file_for_a_person_without_the_link(box):
    pca.page_data()
    pca_music.next_song("lift")
    assert not pca_link.storage.path(pca_link._queue_name("server")).exists()


def test_keep_only_extends(box):
    pca_link.active(1000)
    assert pca_link.keep(600) is False and pca_link._own_queue()["active_until"] == box["now"].timestamp() + 1000
    assert pca_link.keep(1100) is True and pca_link.keep(float("nan")) is False and pca_link.keep("x") is False


# ---- the pairing code route ----------------------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    server = appserver.AppServer(lambda uid: None)
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(server.app)


def test_pair_code_route_needs_auth_and_works_once(client):
    from tools import voice
    assert client.post("/api/link/paircode").status_code == 401
    voice.pair_codes["818181"] = "owner"
    tok = client.post("/api/pair", json={"code": "818181", "device": "phone"}).json()["token"]
    r = client.post("/api/link/paircode", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and len(r.json()["code"]) == 6 and r.json()["expires_in"] == 900
    code = r.json()["code"]
    again = client.post("/api/link/paircode", headers={"Authorization": f"Bearer {tok}"}).json()["code"]
    assert client.post("/api/pair", json={"code": code, "device": "jarvis-link"}).status_code == 403   # replaced by the new one
    ok = client.post("/api/pair", json={"code": again, "device": "jarvis-link"})
    assert ok.status_code == 200 and pca_link.paired("owner")
    assert client.post("/api/pair", json={"code": again, "device": "jarvis-link"}).status_code == 403  # one use
    link = {"Authorization": f"Bearer {ok.json()['token']}"}
    assert client.post("/api/link/paircode", headers=link).status_code == 403                            # not from the app itself


# ---- the page contract, the page, fuzz ------------------------------------------------------------------------------------
def test_page_data_contract_and_docstring(phone):
    lk = pca.page_data()["audio"]["dj"]["link"]
    assert set(lk) == {"paired", "connected", "last_contact", "apk", "auto_dj", "stop_at_end", "playing", "pending", "last"}
    assert lk["paired"] and lk["connected"] and lk["playing"] is None    # the phone reported no title yet
    for word in ('"link"', "music_pause", "music_resume", '"music_link"', '"music_state"', "metrolist_queue", "/api/link/paircode"):
        assert word in pca.__doc__, word


def test_page_has_the_link_pieces():
    html = (Path(__file__).resolve().parent.parent / "app" / "captain.html").read_text(encoding="utf-8")
    for needle in ("JARVIS LINK", "/api/link/apk", "/api/link/paircode", "it does not read your notifications", 'data-act="dj-pause"',
                   "Jarvis is controlling Metrolist", "Playing from Metrolist's queue", '"music_link"', '"music_state"',
                   "Let Jarvis run the music hands-free", "Allow background running"):
        assert needle in html, needle


@pytest.mark.parametrize("seed", range(4))
def test_fuzz_the_new_actions(phone, seed):
    rng = random.Random(seed)
    junk = [None, 0, -1, 1e308, float("nan"), "", "x" * 500, [], {}, {"a": 1}, True, "yes", "pause", "\x00\x1f", "".join(rng.choice(string.printable) for _ in range(30))]
    pca_music.next_song("lift")
    for _ in range(60):
        action = rng.choice(["music_play", "music_pause", "music_resume", "music_link", "music_state", "music_like", "music_unlike", "music_skip", "music_next"])
        body = {"action": action}
        for k in ("auto_dj", "stop_at_end", "id", "phase", "reason", "source", "bpm", "run_kind"):
            if rng.random() < 0.4:
                body[k] = rng.choice(junk)
        out = pca.page_action(body)
        assert isinstance(out, (str, dict)) and out
        if rng.random() < 0.3:
            phone.poll()
        if rng.random() < 0.2:
            phone.report(title=rng.choice(["Rich Flex", "", None, "x" * 400]), artist=rng.choice(["Drake", "", "\x00"]),
                         video=rng.choice(["", "RichFlex001", "bad"]), media_id=rng.choice([None, "", "auto:queue:x:!!", "garbage"]))
    assert isinstance(pca_music.tick(), dict)


@pytest.mark.parametrize("media,want", [("auto:queue:12:" + b64("RichFlex001"), "RichFlex001"), ("RichFlex001", "RichFlex001"),
                                        ("auto:song:" + b64("RichFlex001"), "RichFlex001"), ("", ""), ("auto:queue:1:!!", ""),
                                        ("auto:queue:1:" + b64("short"), ""), (None, "")])
def test_video_id_from_the_media_id(media, want):
    assert pca_music.vid_of(media) == want


# ---- voice -----------------------------------------------------------------------------------------------------------------
ON = {"on": True, "current": None, "adjusted": False, "pain": [], "open": True, "music": {"enabled": True, "recent": True, "prompted": False}}
NEW = [("pause the music", "music_pause"), ("Jarvis, pause the music please", "music_pause"), ("pause my music", "music_pause"),
       ("resume the music", "music_resume"), ("unpause the music", "music_resume"), ("turn the music back on", "music_resume"),
       ("play the music again", "music_resume"), ("what song is this", "music_what"), ("what's this song", "music_what"),
       ("what's playing", "music_what"), ("what song is playing", "music_what"), ("who sings this song", "music_what")]
OLD = [("skip this song", "music_skip"), ("next song", "music_next"), ("I like this song", "music_like"),
       ("play a motivational speech", "music_speech"), ("play an army cadence", "music_cadence"), ("change the music", "music_next")]


@pytest.mark.parametrize("said,skill", NEW)
def test_new_voice_phrases(said, skill):
    linked = dict(ON, music=dict(ON["music"], link=True))                                       # the phone's Link is reachable
    assert coachintent.call_for(said, None, True, linked) == (skill, {})
    assert coachintent.call_for(said, {"prompted": True}, True, linked) == (skill, {})
    assert coachintent.call_for(said, None, True, dict(linked, open=False)) == (skill, {})       # music picked in the last 30 min
    assert coachintent.call_for(said, None, True, ON) is None                                   # no reachable Link: not captured
    assert coachintent.maybe(said, pca_on=True)


def test_every_music_phrase_matches_exactly_one_rule():
    for said, skill in NEW + OLD:
        hit = [s for s, bare, rx in coachintent._MUSIC if rx.match(said)]
        assert hit == [skill], (said, hit)


@pytest.mark.parametrize("said", [s for s, _ in NEW + OLD])
def test_without_the_program_or_the_picker_routing_is_as_before(said):
    assert coachintent.call_for(said, None, True, None) is None
    assert coachintent.call_for(said, None, False, None) is None
    assert coachintent.call_for(said, None, True, {"on": False}) is None
    assert coachintent.call_for(said, None, True, dict(ON, music={"enabled": False, "recent": True})) is None
    assert coachintent.call_for(said, None, True, dict(ON, open=False, music={"enabled": True, "recent": False})) is None
    assert not coachintent.maybe(said) or coachintent._MAYBE.search(said)         # the trainer's own words are unchanged


@pytest.mark.parametrize("said", ["pause", "resume", "what is this", "who is this", "pause the video", "play it again",
                                  "what's playing tonight at the cinema", "stop"])
def test_new_negatives(said):
    hit = coachintent.call_for(said, None, True, ON)
    assert not hit or not hit[0].startswith("music_"), (said, hit)


def test_voice_skills_are_registered():
    names = pca.registry.names()
    assert {"music_pause", "music_resume", "music_what"} <= set(names)


def test_play_resends_the_current_pick_without_a_new_pick(phone):
    assert pca.page_action({"action": "music_play"}).startswith("There is no song of mine")
    pca_music.next_song("lift")
    plays = len([e for e in music() if e["act"] == "play"])
    now = pca_music.music_state()["now"]
    assert pca.page_action({"action": "music_play"}).endswith("Sending it to your phone.")
    phone.poll()
    assert phone.got[-1][0] == "play_id" and phone.got[-1][1]["video_id"] == now["id"]
    assert len([e for e in music() if e["act"] == "play"]) == plays     # nothing new recorded


def test_the_phones_requests_run_the_auto_dj(client, monkeypatch):
    from tools import voice
    ran = []
    monkeypatch.setattr(pca_music, "tick_soon", lambda uid: ran.append(uid) or True)
    voice.pair_codes["828282"] = "owner"
    tok = client.post("/api/pair", json={"code": "828282", "device": "jarvis-link"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/link/state", headers=h, json={"playing": True, "title": "Song"}).status_code == 200
    assert client.get("/api/link/cmd?wait=0", headers=h).status_code == 200
    assert ran == ["owner", "owner"]
