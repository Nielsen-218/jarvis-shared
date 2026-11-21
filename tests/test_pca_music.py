"""Jarvis picks the workout music (core/pca_music rules + tools/pca_music API + the page/voice wiring).

Pure rules with a seeded random.Random and a scripted pool; the API on sandboxed storage (tests/conftest.py) with the
search replaced by a recorder: no network, no model, no speaker."""
import hashlib
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import coachintent
from core import pca_music as m
from tools import pca, pca_music, trainer

HIS = "Dababy baby chief sleepy hollow post Malone and more idc I like good music not country tho lol"
MON = datetime(2026, 11, 23, 7, 0)
ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


def vid(q: str, i: int) -> str:
    return hashlib.md5(f"{q}|{i}".encode()).hexdigest()[:11]


def results(q: str, n: int = 4) -> list[dict]:
    """What a search for q 'returns': songs; speeches and cadences where the query asks for them."""
    if "speech" in q:
        return [{"id": vid(q, i), "title": f"Discipline motivational speech {i}", "channel": "Speeches", "duration_s": 300 + 200 * i} for i in range(n)]
    if "cadence" in q:
        return [{"id": vid(q, i), "title": f"Army running cadence {i}", "channel": "Cadence", "duration_s": 240 + 300 * i} for i in range(n)]
    return [{"id": vid(q, i), "title": f"{q} track {i}", "channel": "Chan", "duration_s": 180 + 10 * i} for i in range(n)]


def pool_for(phase, t, ctx=None) -> dict:
    return {q: results(q) for s in m.SOURCES for a in ([""] + list(t["artists"])) for q in m.queries(s, phase, t, a, ctx)}


@pytest.fixture
def his():
    return m.taste(HIS)


# ---- taste -----------------------------------------------------------------------------------------------------------
def test_his_exact_sentence(his):
    assert his["artists"] == ["DaBaby", "Lil Baby", "Chief Keef", "Sleepy Hallow", "Post Malone"]
    assert his["genres"] == ["hip hop", "trap", "drill"]
    assert his["avoid"] == ["country"] and his["open"] is True
    assert his["cadence"] is True and his["tempo"] is True             # he asked for Army cadence and tempo music too


def test_unknown_names_are_kept_and_saved_lists_win():
    t = m.taste("Drake, Rylo Rodriguez, no edm")
    assert t["artists"] == ["Drake", "Rylo Rodriguez"] and t["avoid"] == ["edm"] and t["open"] is False
    t = m.taste(HIS, {"artists": ["sleepy hollow", "Rylo"], "avoid": ["country", "Drake"], "open": False, "cadence": False})
    assert t["artists"] == ["Sleepy Hallow", "Rylo"] and t["avoid"] == ["country", "Drake"] and not t["open"] and not t["cadence"]
    assert m.taste("anything but country")["avoid"] == ["country"]


# ---- recipes ---------------------------------------------------------------------------------------------------------
def test_recipes(his):
    assert m.recipe("hard", his) == {"artist": 0.30, "similar": 0.20, "workout": 0.40, "cadence": 0.10}   # no speech on intervals
    assert set(m.recipe("cooldown", his)) == {"calm", "lofi"}                                          # never hype, speech or cadence
    assert m.recipe("brief", his) == {"speech": 0.7, "cadence": 0.3}
    assert m.recipe("lift", his) == {"artist": 0.45, "similar": 0.25, "workout": 0.20, "speech": 0.10}
    assert "cadence" not in m.recipe("warmup", his) and m.recipe("warmup", his, {"run": True})["cadence"] == 0.10
    assert m.recipe("easy", his)["cadence"] == 0.15 and m.recipe("easy", his)["speech"] == 0.25
    assert "similar" not in m.recipe("lift", dict(his, open=False)) and "cadence" not in m.recipe("hard", dict(his, cadence=False))


def test_queries_never_search_an_avoided_genre_and_tempo_uses_his_cadence(his):
    every = [q for s in m.SOURCES for ph in m.PHASES for q in m.queries(s, ph, his)]
    assert every and not any("country" in q for q in every)
    assert m.queries("workout", "hard", his, ctx={"run_kind": "tempo", "bpm": 178})[:2] == ["tempo run music 178 bpm", "running music 178 bpm hip hop"]
    assert m.queries("workout", "hard", his)[0] == "tempo run music 170 bpm"
    assert "army running cadence" in m.queries("cadence", "easy", his)
    assert m.queries("workout", "lift", his)[:2] == ["hip hop workout music", "gym motivation hip hop"]


# ---- acceptable ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("title,secs,phase,source,ok", [
    ("DaBaby - Suge", 200, "lift", "artist", True),
    ("DaBaby - Suge", 60, "lift", "artist", False),                     # under 90 s
    ("DaBaby - Suge", 500, "lift", "artist", False),                    # over 7 min
    ("Top hits full album", 200, "lift", "artist", False),
    ("Workout playlist 2026", 200, "lift", "workout", False),
    ("1 hour gym music", 200, "lift", "workout", False),
    ("Gym mix", 900, "lift", "workout", False),                         # a long mix
    ("Gym mix", 300, "lift", "workout", True),
    ("Suge (slowed)", 200, "lift", "artist", False),
    ("Suge sped up", 200, "lift", "artist", False),
    ("Suge 8D audio", 200, "lift", "artist", False),
    ("Suge reaction", 200, "lift", "artist", False),
    ("Chill instrumental", 200, "cooldown", "calm", True),              # instrumental is fine to cool down
    ("Chill instrumental", 200, "lift", "workout", False),
    ("Best country workout", 200, "lift", "workout", False),            # he avoids country
    ("Motivational speech - discipline", 200, "lift", "workout", False),  # a speech is not a song
    ("Motivational speech - discipline", 600, "lift", "speech", True),
    ("Motivational speech - discipline", 90, "lift", "speech", False),  # speeches 2-20 min
    ("Motivational speech - discipline", 400, "brief", "speech", False),  # the briefing: under 6 min
    ("Motivational speech - discipline", 300, "brief", "speech", True),
    ("Army running cadence compilation", 1500, "easy", "cadence", True),  # long compilations are fine for cadence
    ("Army running cadence mix", 2000, "easy", "cadence", False),       # but not over 30 min
    ("Army running cadence", 60, "easy", "cadence", False),
    ("Army running cadence", 400, "brief", "cadence", False),
    ("Some song", 300, "easy", "cadence", False),                       # not a cadence
])
def test_acceptable_table(his, title, secs, phase, source, ok):
    assert m.acceptable({"id": "abcdefghijk", "title": title, "channel": "x", "duration_s": secs}, phase, source, his)[0] is ok


def test_acceptable_drops_duplicates_by_id_and_title(his):
    c = {"id": "abcdefghijk", "title": "DaBaby - Suge (Official Audio)", "channel": "x", "duration_s": 200}
    assert m.acceptable(c, "lift", "artist", his, {"abcdefghijk"})[1] == "duplicate"
    assert m.acceptable(dict(c, id="zzzzzzzzzzz"), "lift", "artist", his, {m.norm_title("DaBaby - Suge [Official Video]")})[1] == "duplicate"
    assert not m.acceptable(dict(c, duration_s=None), "lift", "artist", his)[0]


# ---- learning --------------------------------------------------------------------------------------------------------
def ev(act, source="artist", artist="DaBaby", phase="lift", i="aaaaaaaaaaa", date="2026-11-23", title="t"):
    return {"type": "music", "act": act, "source": source, "artist": artist, "phase": phase, "id": i, "date": date, "title": title}


def test_like_skip_neutral_weights_with_floor_and_cap(his):
    base = m.weights("lift", [], his)
    assert m.weights("lift", [ev("like")], his)["artist"] == pytest.approx(base["artist"] * 1.25)
    assert m.weights("lift", [ev("skip")], his)["artist"] == pytest.approx(base["artist"] * 0.85)
    assert m.weights("lift", [ev("neutral")], his)["artist"] == pytest.approx(base["artist"] * 1.03)
    assert m.weights("lift", [ev("like")] * 20, his)["artist"] == pytest.approx(base["artist"] * 3.0)
    floored = m.weights("lift", [ev("skip", "speech")] * 30, his)
    assert floored["speech"] == pytest.approx(0.05 * sum(m.weights("lift", [ev("skip", "speech")] * 30, his).values()), rel=0.2)
    assert floored["speech"] > 0                                       # he can drift back
    assert m.weights("hard", [ev("skip")] * 5, his)["artist"] == pytest.approx(0.30)   # a skip counts in its own phase only
    aw = m.artist_weights("lift", [ev("like", artist="Lil Baby")] * 10 + [ev("skip", i="b" * 11)] * 10, his)
    assert aw["Lil Baby"] == 3.0 and aw["DaBaby"] == 0.0 or aw["DaBaby"] == pytest.approx(0.3)


def test_two_skips_in_a_row_rest_an_artist_for_the_day(his):
    two = [ev("skip", i="a" * 11), ev("skip", i="b" * 11)]
    assert m.resting(two, "2026-11-23") == {"DaBaby"}
    assert m.artist_weights("lift", two, his, "2026-11-23")["DaBaby"] == 0
    assert m.resting(two, "2026-11-24") == set()                        # tomorrow he is back
    assert m.resting([ev("skip", i="a" * 11), ev("like", i="c" * 11), ev("skip", i="b" * 11)], "2026-11-23") == set()


def test_choose_is_exact_with_a_seed_and_respects_history(his):
    pool = pool_for("lift", his)
    a = m.choose("lift", his, [], pool, random.Random(7))
    b = m.choose("lift", his, [], pool, random.Random(7))
    assert a == b and a["id"] and a["why"] and a["source"] in m.recipe("lift", his)
    played = [dict(a, type="music", act="play", date="2026-11-23")]
    for n in range(60):                                                  # no repeat within 40 plays
        s = m.choose("lift", his, played, pool, random.Random(n))
        assert s["id"] not in {p["id"] for p in played[-40:]}
        played.append(dict(s, type="music", act="play", date="2026-11-23"))
    skipped = [dict(a, type="music", act="play"), dict(a, type="music", act="skip")] + [dict(ev("play"), id=f"x{n:010d}") for n in range(45)]
    for n in range(30):                                                  # a skipped song never comes back
        assert m.choose("lift", his, skipped, pool, random.Random(n))["id"] != a["id"]


def test_why_lines_and_hints(his):
    pool = pool_for("lift", his)
    s = m.choose("lift", his, [], pool, random.Random(1), hint="artist")
    assert s["source"] == "artist" and s["why"] == f"Because you listed {s['artist']}."
    s = m.choose("lift", his, [], pool, random.Random(1), hint="speech")
    assert s["source"] == "speech" and s["why"] == "A motivational speech for the lifting."
    s = m.choose("lift", his, [], pool, random.Random(1), hint="surprise")
    assert s["source"] == "similar" and s["why"] == "Hip hop like the artists you listed."
    s = m.choose("easy", his, [], pool_for("easy", his), random.Random(1), hint="cadence")
    assert s["source"] == "cadence" and s["why"] == "An Army running cadence for the run."
    s = m.choose("brief", his, [], pool_for("brief", his), random.Random(3))
    assert s["source"] in ("speech", "cadence") and s["duration_s"] < 360
    hard = pool_for("hard", his, {"run_kind": "intervals"})
    assert m.choose("hard", his, [], hard, random.Random(1), {"run_kind": "intervals"}, hint="speech")["source"] != "speech"


def test_cooldown_is_only_calm(his):
    pool = pool_for("cooldown", his)
    for n in range(40):
        assert m.choose("cooldown", his, [], pool, random.Random(n), hint="cadence")["source"] in ("calm", "lofi")


def test_liked_songs_come_back_rarely(his):
    pool = pool_for("lift", his)
    liked = {"type": "music", "act": "play", "id": "l" * 11, "title": "Liked one", "channel": "c", "duration_s": 200, "source": "artist",
             "artist": "DaBaby", "phase": "lift"}
    plays = [liked, dict(liked, act="like")] + [dict(ev("play"), id=f"p{n:010d}", source="workout") for n in range(25)]
    hits = sum(m.choose("lift", his, plays, pool, random.Random(n))["source"] == "liked" for n in range(200))
    assert 0 < hits < 60                                                 # a few, never most
    recent = [liked, dict(liked, act="like")] + [dict(ev("play"), id=f"p{n:010d}") for n in range(5)]
    assert all(m.choose("lift", his, recent, pool, random.Random(n))["source"] != "liked" for n in range(100))   # not within 25 plays
    after_liked = plays + [dict(liked, act="play", source="liked")]
    assert all(m.choose("lift", his, after_liked, pool, random.Random(n))["source"] != "liked" for n in range(100))  # one in six at most


def test_needs_names_the_searches_and_none_when_the_pool_is_full(his):
    want = m.needs("lift", his, [], {})
    assert 0 < len(want) <= 3 and all("country" not in q for q in want)
    assert m.needs("lift", his, [], pool_for("lift", his)) == []
    assert any("speech" in q for q in m.needs("lift", his, [], {}, hint="speech"))
    assert any("cadence" in q for q in m.needs("easy", his, [], {}, hint="cadence"))


# ---- the API on sandboxed storage ------------------------------------------------------------------------------------
@pytest.fixture
def api(monkeypatch):
    box = {"now": MON, "searched": [], "opened": []}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)
    monkeypatch.setattr(trainer, "SAY", lambda seconds, line, uid: None)
    monkeypatch.setattr(pca, "_open_link", lambda url: box["opened"].append(url))

    def backend(q, n):
        box["searched"].append(q)
        return results(q, 6)
    monkeypatch.setattr(pca_music, "_backend", backend)
    for qid, value in {**ANSWERS, "music_prefs": HIS, "music_service": "metrolist"}.items():
        assert pca.page_action({"action": "answer", "id": qid, "value": value}).startswith("Saved"), qid
    assert pca.audio_text({"service": "metrolist"}) == "Music preferences saved."
    assert "starts today" in pca.page_action({"action": "begin"})
    return box


def music():
    return [e for e in trainer.events() if e["type"] == "music"]


def test_next_song_records_a_play_and_precomputes_upnext(api):
    out = pca_music.next_song("lift")
    assert out["ok"] and out["song"]["link"] == f"https://music.youtube.com/watch?v={out['song']['id']}"
    assert out["upnext"] and out["upnext"]["id"] != out["song"]["id"]
    assert [e["act"] for e in music()] == ["play"] and music()[0]["id"] == out["song"]["id"]
    assert out["message"].startswith(out["song"]["title"]) and out["song"]["why"] in out["message"]
    assert api["searched"] and all("country" not in q for q in api["searched"])
    skip = pca_music.feedback("skip")
    assert skip["song"]["id"] == out["upnext"]["id"]                     # the song the page already opened inside the tap
    assert [e["act"] for e in music()] == ["play", "skip", "play"]
    assert music()[1]["id"] == out["song"]["id"]
    nxt = pca_music.next_song("lift", "next")
    assert [e["act"] for e in music()][-2:] == ["neutral", "play"] and nxt["song"]["id"] != skip["song"]["id"]
    like = pca_music.feedback("like")
    assert like["ok"] and music()[-1]["act"] == "like" and pca_music.music_state()["liked"]
    assert pca_music.feedback("like")["message"].startswith("You already liked") and music()[-1]["act"] == "like"
    assert pca_music.feedback("unlike")["message"] == "Okay, not liked." and music()[-1]["act"] == "unlike"
    st = pca_music.music_state()
    assert st["enabled"] and st["service"] == "metrolist" and st["link_form"] == "watch"
    assert st["stats"] == {"plays": 3, "likes": 1, "skips": 1} and st["taste"]["artists"][0] == "DaBaby"


def test_search_is_cached_and_failure_is_honest(api, monkeypatch):
    pca_music.next_song("lift")
    first = len(api["searched"])
    pca_music.next_song("lift", "next", "artist")
    assert len(api["searched"]) - first <= 3
    for q in api["searched"]:
        assert pca_music.cached(q)                                       # 14-day cache in the person's data folder
    api["now"] = MON + timedelta(days=15)
    assert pca_music.cached(api["searched"][0]) is None

    def broken(q, n):
        raise OSError("no network")
    monkeypatch.setattr(pca_music, "_backend", broken)
    monkeypatch.setattr(pca_music, "_failed", {})
    out = pca_music.next_song("lift", "next", "speech")
    assert not out["ok"] and out["song"] is None and "could not reach YouTube search" in out["message"]
    assert out["fallback_link"].startswith("https://music.youtube.com/search?q=")
    assert pca_music.music_state()["fallback"]["label"].startswith("Search: ") and pca_music.music_state()["now"] is None
    calls = []
    monkeypatch.setattr(pca_music, "_backend", lambda q, n: calls.append(q) or [])
    assert pca_music.search("anything new") == [] and calls == []        # remembered for 10 minutes: no new attempt


def test_other_services_get_a_search_link(api):
    pca.audio_text({"service": "spotify", "dj": True})
    out = pca_music.next_song("lift")
    assert out["song"]["link"].startswith("https://open.spotify.com/search/") and "cannot open a YouTube song" in out["message"]
    assert "cannot open" not in pca_music.next_song("lift", "next")["message"]          # said once


def test_page_contract(api):
    page = pca.page_data()
    dj = page["audio"]["dj"]
    assert dj["enabled"] and dj["now"] is None and dj["taste"]["avoid"] == ["country"]
    assert pca.page_action({"action": "music_next", "phase": "hard", "run_kind": "intervals", "bpm": 176}).endswith(".")
    dj = pca.page_data()["audio"]["dj"]
    assert dj["now"]["phase"] == "hard" and dj["upnext"]
    assert any("176 bpm" in q for q in api["searched"])
    up = dj["upnext"]["id"]
    pca.page_action({"action": "music_skip"})
    assert pca.page_data()["audio"]["dj"]["now"]["id"] == up
    assert pca.page_action({"action": "music_like"}).startswith("Liked")
    assert pca.page_action({"action": "music_neutral"}) == "Noted."
    assert pca.page_action({"action": "music_taste", "artists": ["DaBaby", "Rod Wave"], "open": False, "cadence": True}) == "Music taste saved."
    t = pca.page_data()["audio"]["dj"]["taste"]
    assert t["artists"] == ["DaBaby", "Rod Wave"] and t["open"] is False
    assert pca.page_action({"action": "music_taste", "artists": "DaBaby"}).startswith("Send artists")
    assert pca.page_action({"action": "music_taste", "open": "yes"}).startswith("Send open")
    assert pca.page_action({"action": "music_taste", "avoid": ["x" * 60]}).startswith("Send avoid")
    pca.audio_text({"service": "metrolist", "dj": False})
    assert pca.page_data()["audio"]["dj"]["enabled"] is False


# ---- voice -----------------------------------------------------------------------------------------------------------
ON = {"on": True, "current": None, "adjusted": False, "pain": [], "open": True, "music": {"enabled": True, "recent": True, "prompted": False}}
PICKED = dict(ON, music={"enabled": True, "recent": True, "prompted": True})          # the last thing Jarvis said was a pick
WORDED = [("skip this song", "music_skip"), ("Jarvis, skip this song please", "music_skip"), ("I don't like this song", "music_skip"),
          ("I don't want this track", "music_skip"), ("next song", "music_next"), ("change the music", "music_next"),
          ("change the song", "music_next"), ("switch the track", "music_next"), ("play another song", "music_next"),
          ("I like this song", "music_like"), ("I love this track", "music_like"), ("play a motivational speech", "music_speech"),
          ("play an army cadence", "music_cadence"), ("play running cadence", "music_cadence")]
BARE = [("I don't like this one", "music_skip"), ("I don't want this one", "music_skip"), ("skip it", "music_skip"),
        ("like this one", "music_like"), ("I like this one", "music_like"), ("play something else", "music_next")]


@pytest.mark.parametrize("said,skill", WORDED)
def test_voice_worded_phrases(said, skill):
    assert coachintent.call_for(said, None, True, ON) == (skill, {})
    assert coachintent.call_for(said, None, True, dict(ON, open=False)) == (skill, {})           # a pick in the last 30 minutes
    assert coachintent.call_for(said, {"prompted": True}, True, ON) == (skill, {})              # names the music: not a workout reply
    assert coachintent.maybe(said, pca_on=True)                                                # brain/llm lets the line through


@pytest.mark.parametrize("said,skill", BARE)
def test_voice_bare_phrases_only_right_after_a_pick_line(said, skill):
    assert coachintent.call_for(said, None, True, PICKED) == (skill, {})
    hit = coachintent.call_for(said, None, True, ON)                                           # no pick line just before
    assert not hit or not hit[0].startswith("music_"), hit
    hit = coachintent.call_for(said, {"prompted": True}, True, PICKED)                          # the reply to a workout line
    assert not hit or not hit[0].startswith("music_"), hit
    hit = coachintent.call_for(said, {"prompted": True}, True, ON)
    assert not hit or not hit[0].startswith("music_"), hit


def test_every_phrase_matches_exactly_one_rule():
    for said, skill in WORDED + BARE:
        rules_hit = [(s, bare) for s, bare, rx in coachintent._MUSIC if rx.match(said)]
        assert len(rules_hit) == 1 and rules_hit[0][0] == skill, (said, rules_hit)


@pytest.mark.parametrize("said", ["skip this song", "play a motivational speech", "next song", "play an army cadence", "I like this song"])
def test_voice_needs_the_program_the_switch_and_a_session_or_a_recent_pick(said):
    assert coachintent.call_for(said, None, True, dict(ON, open=False, music={"enabled": True, "recent": False})) is None
    assert coachintent.call_for(said, None, True, dict(ON, music={"enabled": False, "recent": True, "prompted": True})) is None
    assert coachintent.call_for(said, None, True, dict(ON, music=None)) is None
    assert coachintent.call_for(said, None, True, {"on": False}) is None
    assert coachintent.call_for(said, None, False, None) is None


def test_voice_negatives_stay_untouched():
    assert coachintent.call_for("skip this exercise", {"prompted": True}, True, PICKED) == ("workout_skip", {})
    for said in ("I like this", "next", "play something", "skip"):
        hit = coachintent.call_for(said, None, True, PICKED)
        assert not hit or not hit[0].startswith("music_"), said
    assert coachintent.call_for("skip it", {"prompted": True}, True, PICKED) == ("workout_skip", {})


def test_voice_skills_pick_and_open_only_with_auto_open(api):
    assert "music_next" in pca.registry.names() and "music_cadence" in pca.registry.names()
    said = pca.music_next()
    assert "Tap Play on the Captain America page." in said and api["opened"] == []
    assert pca.voice_context()["music"] == {"enabled": True, "recent": True, "link": False}
    pca.audio_text({"service": "metrolist", "auto_open": True})
    said = pca.music_skip()
    assert api["opened"] and api["opened"][0].startswith("https://music.youtube.com/watch?v=") and "Tap Play" not in said
    assert pca.music_like().startswith("Liked")
    assert pca.music_like().startswith("You already liked") and music()[-1]["act"] == "like"     # a second like changes nothing
    assert "speech" in pca.music_speech().lower()
    api["now"] = MON + timedelta(minutes=31)
    assert pca.voice_context()["music"] == {"enabled": True, "recent": False, "link": False}


# ---- review fixes ----------------------------------------------------------------------------------------------------
def test_a_hung_search_is_abandoned_and_never_holds_the_trainer_lock(api, monkeypatch):
    import threading
    import time as _t
    assert pca_music.TIMEOUT_S <= 8 and pca_music.TOTAL_S <= 10
    monkeypatch.setattr(pca_music, "_backend", lambda q, n: threading.Event().wait(30) or [])
    result, waited = {}, []

    def pick():
        t0 = _t.monotonic()
        result["out"] = pca_music.next_song("lift")
        result["took"] = _t.monotonic() - t0
    th = threading.Thread(target=pick)
    th.start()
    _t.sleep(1.0)
    t0 = _t.monotonic()
    with trainer._lock:                                                   # another trainer call while the search hangs
        waited.append(_t.monotonic() - t0)
    th.join(15)
    assert not th.is_alive() and result["took"] < 12, result
    assert waited[0] < 0.5, waited
    assert result["out"]["ok"] is False and "could not reach YouTube search" in result["out"]["message"]
    assert [e for e in music() if e["act"] == "play"] == []


@pytest.mark.parametrize("prefs", [{"service": "metrolist", "dj": False}, {"service": "none"}, {"service": "local"}])
def test_the_off_switch_holds_everywhere(api, monkeypatch, prefs):
    calls = []
    monkeypatch.setattr(pca_music, "_backend", lambda q, n: calls.append(q) or results(q))
    pca.audio_text(prefs)
    for said in (pca.music_next(), pca.music_speech(), pca.music_cadence(), pca.music_skip(), pca.music_like()):
        assert said == pca_music.OFF
    for action in ("music_next", "music_like", "music_unlike", "music_skip", "music_neutral"):
        assert pca.page_action({"action": action}) == pca_music.OFF
    assert pca.page_action({"action": "music_taste", "open": False}) == pca_music.OFF
    assert calls == [] and music() == []
    assert pca.voice_context()["music"]["enabled"] is False and pca.page_data()["audio"]["dj"]["enabled"] is False


def test_no_program_no_picks(monkeypatch):
    calls = []
    monkeypatch.setattr(pca_music, "_backend", lambda q, n: calls.append(q) or results(q))
    pca.audio_text({"service": "metrolist"})
    for action in ("music_next", "music_like", "music_skip"):
        assert pca.page_action({"action": action}) == pca_music.NO_PROGRAM
    assert pca.music_next() == pca_music.NO_PROGRAM
    assert calls == [] and [e for e in trainer.events() if e["type"] == "music"] == []
    assert pca.page_data()["audio"]["dj"]["enabled"] is False


def test_liked_songs_pass_the_same_filter(his):
    speech = {"type": "music", "act": "play", "id": "s" * 11, "title": "Discipline motivational speech", "channel": "c",
              "duration_s": 600, "source": "speech", "artist": "", "phase": "lift"}
    road = {"type": "music", "act": "play", "id": "r" * 11, "title": "Country Roads", "channel": "c", "duration_s": 200,
            "source": "artist", "artist": "DaBaby", "phase": "lift"}
    filler = [dict(ev("play"), id=f"p{n:010d}", source="workout") for n in range(30)]
    events = [speech, dict(speech, act="like"), road, dict(road, act="like")] + filler
    hard = pool_for("hard", his)
    for n in range(300):
        assert m.choose("hard", his, events, hard, random.Random(n))["id"] != "s" * 11          # no speech on a hard run
    lift = pool_for("lift", his)
    picks = {m.choose("lift", his, events, lift, random.Random(n))["id"] for n in range(300)}
    assert "r" * 11 not in picks                                                                # he avoids country
    assert "s" * 11 in picks                                                                    # a liked speech may return where speech fits


def test_bpm_garbage_is_a_sentence(api):
    for bad in (float("inf"), float("-inf"), float("nan"), 1e308, "1e999", "Infinity", -5, "fast"):
        out = pca.page_action({"action": "music_next", "phase": "hard", "bpm": bad})
        assert isinstance(out, str) and out
    assert pca_music._ctx(None, None, float("inf"))["bpm"] == 0 and pca_music._ctx(None, None, 176)["bpm"] == 176


@pytest.mark.parametrize("title,avoid", [("Best Hip-Hop 2024", "hip hop"), ("hiphop gym", "hip hop"), ("Best Rap songs", "hip hop"),
                                         ("slow RnB vibes", "r&b"), ("R & B classics", "r&b"), ("Best R&B", "rnb"),
                                         ("Country Roads", "country"), ("Drake - Headlines", "Drake")])
def test_avoid_catches_the_spellings(his, title, avoid):
    t = dict(his, avoid=[avoid])
    assert not m.acceptable({"id": "abcdefghijk", "title": title, "channel": "x", "duration_s": 200}, "lift", "workout", t)[0]


def test_avoid_does_not_overreach(his):
    t = dict(his, avoid=["hip hop"])
    assert m.acceptable({"id": "abcdefghijk", "title": "Trap workout", "channel": "x", "duration_s": 200}, "lift", "workout", t)[0]


def test_an_empty_answer_is_remembered_for_30_minutes(api, monkeypatch):
    calls = []
    monkeypatch.setattr(pca_music, "_backend", lambda q, n: calls.append(q) or [])
    assert pca_music.search("nothing here") == [] and pca_music.search("nothing here") == [] and calls == ["nothing here"]
    api["now"] = MON + timedelta(minutes=31)
    pca_music.search("nothing here")
    assert calls == ["nothing here", "nothing here"]


def test_page_like_toggles_through_unlike(api):
    pca_music.next_song("lift")
    assert pca.page_action({"action": "music_like"}).startswith("Liked")
    assert pca.page_action({"action": "music_like"}).startswith("You already liked")
    assert pca.page_action({"action": "music_unlike"}) == "Okay, not liked."
    assert [e["act"] for e in music()][-2:] == ["like", "unlike"] and not pca_music.music_state()["liked"]


def test_page_has_the_dj_card():
    html = (Path(__file__).resolve().parent.parent / "app" / "captain.html").read_text(encoding="utf-8")
    for needle in ('data-act="dj-skip"', 'data-act="dj-like"', 'data-act="dj-play"', '"music_next"', '"music_taste"', "Cadence",
                   "playback and licensing stay with your music app", "Advanced: my own playlist links"):
        assert needle in html, needle


# ---- review round 2 --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("title,avoid,dropped", [
    ("House Party", "house", True), ("House Music Gym Mix", "house", True), ("Techno Mix", "house", False),
    ("Electronic Mix", "edm", False), ("Techno gym", "edm", False), ("EDM gym", "edm", True),
    ("Best Hip-Hop 2024", "hip hop", True), ("Best Rap songs", "hip hop", True), ("Hip hop gym", "rap", True),
    ("RnB slow jams", "r&b", True), ("R and B classics", "rnb", True), ("Trap workout", "hip hop", False),
    ("Drill workout", "trap", False)])
def test_avoid_matches_his_own_words_and_true_spellings_only(his, title, avoid, dropped):
    t = dict(his, avoid=[avoid])
    ok = m.acceptable({"id": "abcdefghijk", "title": title, "channel": "x", "duration_s": 200}, "lift", "workout", t)[0]
    assert ok is (not dropped), (title, avoid)


@pytest.fixture
def brain():
    from brain import llm
    b = llm.Brain.__new__(llm.Brain)
    b.uid, b.messages = "owner", []
    return b


def _turn(brain, line):
    brain._begin_turn()
    said = []
    return brain._trainer_request(line, said.append), said


def test_a_bare_reply_counts_only_right_after_the_pick_line(api, brain):
    pca_music.next_song("lift")                                           # a pick in the last 30 minutes
    took, said = _turn(brain, "next song")                                # Jarvis's pick line
    assert took and said
    took, _ = _turn(brain, "skip it")                                     # straight after: his
    assert took and music()[-2]["act"] == "skip"
    _turn(brain, "next song")
    brain._begin_turn()                                                   # a turn another route answered (a lesson, a quiz...)
    took, _ = _turn(brain, "I like this one")
    assert not took and music()[-1]["act"] != "like"
    _turn(brain, "next song")
    api["now"] += timedelta(seconds=91)                                   # 91 s later: not a reply any more
    took, _ = _turn(brain, "skip it")
    assert not took
