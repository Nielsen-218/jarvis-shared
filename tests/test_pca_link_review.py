"""Review fixes for Jarvis Link music (round 1): request bodies, stop at the end, failing replacements, the queue-track
filter, voice routing without a reachable Link, per-machine bookkeeping. Same fakes as tests/test_pca_link_dj.py."""
from datetime import timedelta

import pytest

from core import coachintent
from tools import pca, pca_link, pca_music, trainer

from test_pca_link_dj import ON, Phone, _pair_link, autos, box, client, phone, session  # noqa: F401  (fixtures)


# ---- 1. /api/link/* bodies ---------------------------------------------------------------------------------------------
def _link_headers(client):
    from tools import voice
    voice.pair_codes["848484"] = "owner"
    tok = client.post("/api/pair", json={"code": "848484", "device": "jarvis-link"}).json()["token"]
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def test_deep_or_big_bodies_are_400_or_413_never_500(client):
    h = _link_headers(client)
    deep = '{"a":' * 5000 + "1" + "}" * 5000
    assert client.post("/api/link/state", headers=h, content=deep).status_code == 400
    eleven = '{"a":' * 11 + "1" + "}" * 11
    assert client.post("/api/link/state", headers=h, content=eleven).status_code == 400
    assert client.post("/api/link/result", headers=h, content="[" * 3000 + "]" * 3000).status_code == 400
    big = '{"title": "' + "x" * 40000 + '"}'
    assert client.post("/api/link/state", headers=h, content=big).status_code == 413
    assert client.post("/api/link/state", headers=h, content=b"\xff\xfe not json").status_code == 400
    assert client.post("/api/link/state", headers=h, json={"playing": True, "title": "ok"}).status_code == 200


def test_body_cap_reads_no_more_than_the_cap():
    from core import appserver
    assert appserver.LINK_BODY_MAX == 32 * 1024 and appserver.LINK_BODY_DEPTH == 10
    assert appserver._json_depth({"a": [{"b": 1}]}) == 3
    assert appserver._json_depth(1) == 0


# ---- 2. stop at the end -------------------------------------------------------------------------------------------------
def _end_after_jarvis_played(phone, box):
    pca.page_action({"action": "music_link", "stop_at_end": True})
    pca.page_action({"action": "start"})
    pca_music.tick()
    phone.poll()                                                          # Jarvis's pick plays
    pca.page_action({"action": "end"})


def test_stop_at_end_needs_auto_on(phone, box, monkeypatch):
    monkeypatch.setattr(pca, "audio_phase", lambda day: "lift")
    _end_after_jarvis_played(phone, box)
    pca.page_action({"action": "music_link", "auto_dj": False})
    box["now"] += timedelta(seconds=30)
    assert pca_music.tick()["action"] == "none"
    phone.poll()
    assert "pause" not in phone.names()


def test_stop_at_end_only_right_after_the_end(phone, box, monkeypatch):
    monkeypatch.setattr(pca, "audio_phase", lambda day: "lift")
    _end_after_jarvis_played(phone, box)
    box["now"] += timedelta(hours=3)
    phone.report(title=phone.track["title"], artist=phone.track["artist"], media_id=phone.track["media_id"])
    assert pca_music.tick()["action"] == "none"
    phone.poll()
    assert "pause" not in phone.names()


def test_stop_at_end_leaves_his_own_music_alone(phone, box, monkeypatch):
    monkeypatch.setattr(pca, "audio_phase", lambda day: "lift")
    _end_after_jarvis_played(phone, box)
    phone.report(title="His own podcast", artist="Someone", video="HisOwnPod01")    # he started something else
    # a track Jarvis never saw in this session: not his to stop
    box["now"] += timedelta(seconds=30)
    assert pca_music.tick()["action"] == "none"
    phone.poll()
    assert "pause" not in phone.names()


def test_stop_at_end_once(phone, box, monkeypatch):
    monkeypatch.setattr(pca, "audio_phase", lambda day: "lift")
    _end_after_jarvis_played(phone, box)
    box["now"] += timedelta(seconds=30)
    assert pca_music.tick()["action"] == "stop"
    box["now"] += timedelta(seconds=10)
    assert pca_music.tick()["action"] == "none"


# ---- 3. a failing replacement backs off ----------------------------------------------------------------------------------
def test_failed_replacement_is_tried_once_then_backs_off(session, phone, box, monkeypatch):
    pca_music.tick()
    phone.poll()
    monkeypatch.setattr(pca_music, "_pick", lambda *a, **k: {"ok": False, "song": None, "upnext": None,
                                                              "message": "The search found nothing fit to play just now."})
    box["now"] += timedelta(seconds=30)
    phone.report(title="Country Roads", artist="John Denver", video="CountryRoad")
    for _ in range(12):
        pca_music.tick()
        box["now"] += timedelta(seconds=21)
        phone.report(title="Country Roads", artist="John Denver", video="CountryRoad")
    assert len([e for e in autos() if e["what"] == "replace_failed"]) == 1
    phone.report(title="Wagon Wheel", artist="Darius Rucker country", video="WagonWheel1")
    assert pca_music.tick()["action"] == "wait"                           # backing off: no new attempt for 5 minutes
    box["now"] += timedelta(minutes=5)
    phone.report(title="Wagon Wheel", artist="Darius Rucker country", video="WagonWheel1")
    assert pca_music.tick()["action"] == "replace"


def test_auto_events_are_capped_per_session(session, phone, box, monkeypatch):
    monkeypatch.setattr(pca_music, "AUTO_MAX", 5)
    pca_music.tick()
    phone.poll()
    for n in range(12):
        box["now"] += timedelta(seconds=25)
        phone.report(title=f"Fine song {n}", artist="Drake", video=f"FineSong{n:03d}")
        pca_music.tick()
    assert len(autos()) == 5


# ---- 4. the queue-track filter --------------------------------------------------------------------------------------------
REVIEW_TITLES = [("Hours", "Kyle"), ("Midnight Hour", "Skrillex"), ("Trailer Park", "Kid Rock"), ("Interview", "Lil Baby"),
                 ("Review", "Drake"), ("Playlist", "Future"), ("Motivation", "Kelly Rowland"), ("Turn Up", "Chris Brown"),
                 ("Rage", "Playboi Carti"), ("Beast", "Rob Bailey"), ("Cupid's Chokehold", "Gym Class Heroes"),
                 ("8D Love", "Somebody"), ("Hip Hop Workout", "DJ"), ("Beast Mode", "Metallica"), ("Gym Hype", "Drake"),
                 ("A 9 Minute Song", "Band")]


@pytest.mark.parametrize("phase", ["lift", "hard", "easy", "warmup"])
@pytest.mark.parametrize("title,artist", REVIEW_TITLES)
def test_ordinary_queue_tracks_are_left_alone(box, phase, title, artist):
    t = pca_music.taste_now()
    p = {"title": title, "artist": artist, "id": None, "duration_ms": 9 * 60 * 1000 if "9 Minute" in title else None}
    assert pca_music.queue_ok(p, phase, t, [])[0], (title, phase)


@pytest.mark.parametrize("title,artist", REVIEW_TITLES)
def test_cooldown_replaces_only_hype_genre_artist_with_a_hype_title(box, title, artist):
    t = pca_music.taste_now()
    ok, why = pca_music.queue_ok({"title": title, "artist": artist, "id": None}, "cooldown", t, [])
    replaced = (title, artist) == ("Beast Mode", "Metallica")             # metal (a hype genre) AND a hype word in the title
    assert ok is not replaced, (title, artist, why)


@pytest.mark.parametrize("title,artist,phase,why", [
    ("Country Roads", "John Denver", "lift", "avoided"), ("Fishin' in the Dark", "Country Band", "lift", "avoided"),
    ("Discipline - Motivational Speech", "Speeches", "hard", "speech"), ("Episode 12", "The Gym Podcast", "lift", "speech"),
    ("Sunday Sermon", "Pastor", "hard", "speech")])
def test_queue_tracks_that_are_replaced(box, title, artist, phase, why):
    ok, reason = pca_music.queue_ok({"title": title, "artist": artist, "id": None}, phase, pca_music.taste_now(), [])
    assert not ok and why in reason, (title, reason)


def test_skipped_track_or_twice_skipped_artist_is_replaced(box):
    t = pca_music.taste_now()
    ev = lambda title, artist, vid: {"type": "music", "act": "skip", "id": vid, "title": title, "artist": artist}  # noqa: E731
    assert not pca_music.queue_ok({"title": "Rich Flex", "artist": "Drake", "id": "RichFlex001"}, "lift", t,
                                  [ev("Rich Flex", "Drake", "RichFlex001")])[0]
    one = [ev("A", "Nav", "AAAAAAAAAAA")]
    assert pca_music.queue_ok({"title": "B", "artist": "Nav", "id": "BBBBBBBBBBB"}, "lift", t, one)[0]
    two = one + [ev("C", "Nav", "CCCCCCCCCCC")]
    assert not pca_music.queue_ok({"title": "B", "artist": "Nav", "id": "BBBBBBBBBBB"}, "lift", t, two)[0]


# ---- 5. voice needs a reachable Link ------------------------------------------------------------------------------------------
LINK_PHRASES = ["pause the music", "resume the music", "what song is this", "what's playing"]


@pytest.mark.parametrize("said", LINK_PHRASES)
def test_link_phrases_need_a_reachable_link(said):
    reach = dict(ON, music={"enabled": True, "recent": True, "prompted": False, "link": True})
    assert coachintent.call_for(said, None, True, reach) is not None
    for ctx in (ON, dict(ON, music={"enabled": True, "recent": True, "link": False})):         # picker on, session open
        assert coachintent.call_for(said, None, True, ctx) is None, (said, ctx)


def test_voice_context_reports_the_link(phone):
    pca_music.next_song("lift")
    assert pca.voice_context()["music"]["link"] is True
    phone.box["now"] += timedelta(seconds=91)
    assert pca.voice_context()["music"]["link"] is False


# ---- 7. per-machine bookkeeping -----------------------------------------------------------------------------------------------
def test_link_and_auto_bookkeeping_has_one_writer_machine(session, phone, box, monkeypatch):
    from core import storage
    pca_music.tick()
    phone.poll()
    shared = storage.load(pca_music.STATE, {})
    assert "auto" not in shared and "link" not in shared
    mine = storage.load("fitness/music-auto-server.json", {})
    assert mine.get("auto") and mine.get("link")
    box["now"] += timedelta(seconds=1)
    monkeypatch.setattr(pca_link, "machine", lambda: "pc")              # the PC sends a pause by voice
    monkeypatch.setattr(pca_music, "_machine", lambda: "pc")
    pca_music.control("pause")
    assert storage.load("fitness/music-auto-pc.json", {})["link"]["last"]["what"] == "pause"
    assert storage.load("fitness/music-auto-server.json", {})["link"]["last"]["what"] == "play"
    assert pca_music.music_state()["link"]["last"]["what"] == "pause"   # reads merge both: the newest wins


# ---- pairing without typing (Jarvis Link 1.3): the owner could not get through "read a code, type it in the app" ----
@pytest.mark.skip(reason="needs the built Android APK (not shipped)")
def test_one_tap_pairing_is_wired_in_app_and_page():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    link = root / "android" / "jarvis-link"
    manifest = (link / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'android:scheme="jarvislink" android:host="pair"' in manifest and "android.intent.category.BROWSABLE" in manifest
    assert 'android:launchMode="singleTop"' in manifest
    main = (link / "app/src/main/java/com/jarvis/link/MainActivity.java").read_text(encoding="utf-8")
    assert "handleLink(getIntent())" in main and "onNewIntent" in main
    # another server's address is never taken from a link without a yes
    assert "base.equalsIgnoreCase(here)" in main and "Connect to a different Jarvis?" in main
    assert 'code6.equals(prefs.text("link_code"))' in main and '"link_try_at"' in main   # a used code is never re-sent
    assert "url.setText(base); pair(" not in main               # the address field changes only after success
    assert "'--version-code', '4', '--version-name', '1.3'" in (link / "build.ps1").read_text(encoding="utf-8")
    page = (root / "app" / "captain.html").read_text(encoding="utf-8")
    assert "scheme=jarvislink;package=com.jarvis.link;end" in page and 'data-act="link-connect"' in page
    assert 'case "link-connect": await linkConnect(); break;' in page
    assert 'data-act="link-code"' in page                       # typing a code stays as the fallback
    assert "lk.connected, code" in page and "pairbanner-off" not in page   # banner goes by connected, this visit only
    import zipfile
    with zipfile.ZipFile(link / "dist" / "JarvisLink.apk") as z:   # the built app carries the link filter
        assert "jarvislink".encode("utf-16-le") in z.read("AndroidManifest.xml") or b"jarvislink" in z.read("AndroidManifest.xml")
