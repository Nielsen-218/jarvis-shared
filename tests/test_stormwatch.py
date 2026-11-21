"""core/stormwatch + the protocol engine's first outside-data trigger (owner 2026-11-16).

Every input here is RECORDED from the real public feeds (tests/fixtures/stormwatch/README.md says where each came from):
Hurricane Melissa's advisory 18 (NHC, 25 Oct 2025, a Hurricane Watch for Haiti), the NHC active-storms feed of that night
(Wayback Machine copy), the 14 Aug 2021 M7.2 Haiti earthquake (USGS), and real NWS alerts from 16 Nov 2026. Where a test
needs a case the recording does not have (a Hurricane WARNING, an alert for Springfield), it edits one field of a recorded
item and says so. No test reaches a live feed (conftest makes stormwatch._http refuse)."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import attention, notify, pcbridge, storage
from core import protocols as P
from core import stormwatch as S

FIX = Path(__file__).parent / "fixtures" / "stormwatch"
TCM = "https://www.nhc.noaa.gov/text/MIATCMAT3.shtml"
TCP = "https://www.nhc.noaa.gov/text/MIATCPAT3.shtml"
MELISSA_NOW = datetime(2025, 10, 26, 0, 0, tzinfo=timezone.utc)
HAITI_MAIN = datetime.fromtimestamp(1628944148454 / 1000, timezone.utc)     # us6000f65h, M7.2 Nippes


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class Feeds:
    """A `get` that serves recorded files by URL and remembers what was asked."""

    def __init__(self, **over):
        self.files = {S.NHC_CURRENT: fx("nhc_current_20251026.json"), TCM: fx("nhc_melissa_tcm_018.html"),
                      TCP: fx("nhc_melissa_tcp_018.html"), S.USGS_FEED: fx("usgs_4.5_day_20261116.geojson"),
                      S.NWS_AREA.format(area="VA"): fx("nws_va_20261116.json")}
        self.files.update(over)
        self.asked = []

    def __call__(self, url, accept="application/json", timeout=15.0):
        self.asked.append(url)
        body = self.files.get(url)
        if isinstance(body, Exception):
            raise body
        if body is None:
            raise RuntimeError(f"unexpected url {url}")
        return body.encode("utf-8")


def nws_with(event=None, same=("051650",), message="Alert", severity=None, offset_h=2.0, onset_h=None):
    """One real recorded NWS alert (High Wind Warning, 16 Nov 2026) moved onto Springfield's county code (an EDIT, said here)."""
    d = json.loads(fx("nws_us_severe_sample_20261116.json"))
    f = copy.deepcopy(next(x for x in d["features"] if x["properties"]["event"] == "High Wind Warning"))
    p = f["properties"]
    p["geocode"]["SAME"] = list(same)
    p["messageType"] = message
    if event:
        p["event"] = event
    if severity:
        p["severity"] = severity
    sent = datetime.fromisoformat(p["sent"])
    p["expires"] = p["ends"] = (sent + timedelta(hours=offset_h)).isoformat()
    if onset_h is not None:
        p["onset"] = p["effective"] = (sent + timedelta(hours=onset_h)).isoformat()
    return json.dumps({"features": [f]}), sent


@pytest.fixture
def pc(monkeypatch):
    """This machine is the PC; no laptop unless a test says so."""
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.delenv("JARVIS_SERVER_URL", raising=False)
    monkeypatch.setenv("JARVIS_ROLE", "all")


@pytest.fixture
def out(monkeypatch):
    """Record what would be said (core.attention) and pushed (core.notify)."""
    said, pushed = [], []

    def deliver(text, urgency=attention.NORMAL, key="", sit=None):
        said.append({"text": text, "urgency": urgency, "key": key, "sit": sit})
        return "said" if urgency == attention.CRITICAL or (sit or {}).get("present") else "held"
    monkeypatch.setattr(attention, "deliver", deliver)
    monkeypatch.setattr(attention, "situation", lambda: {"present": False, "gaming": "", "idle": 9999})
    monkeypatch.setattr(notify, "notify", lambda title, message, uid=None, category="jarvis", urgent=None, extra=None:
                        pushed.append({"title": title, "text": message, "category": category, "urgent": urgent}))
    monkeypatch.setattr(S, "quiet_now", lambda uid=None: False)
    monkeypatch.setattr(P, "ACTIONS", {**P.ACTIONS, "warn": P._warn, "ping": P._ping})   # only these two are real here
    return said, pushed


# -- parsing the recorded NHC products ---------------------------------------------------------------------------------
def test_forecast_track_from_melissa_advisory_18():
    text = S.pre_text(fx("nhc_melissa_tcm_018.html"))
    base = S.issued(text)
    assert base == datetime(2025, 10, 25, 21, 0, tzinfo=timezone.utc)
    track = S.parse_track(text, base)
    assert [p["kind"] for p in track] == ["now"] + ["forecast"] * 6 + ["outlook"] * 2
    assert (track[0]["lat"], track[0]["lon"], track[0]["kt"]) == (16.6, -75.5, 80)
    assert (track[1]["lat"], track[1]["lon"], track[1]["kt"]) == (16.7, -75.9, 100)
    assert track[1]["when"] == datetime(2025, 10, 26, 6, 0, tzinfo=timezone.utc)
    assert (track[-1]["lat"], track[-1]["lon"], track[-1]["kt"], track[-1]["when"].day) == (27.0, -69.0, 80, 30)


def test_watches_and_warnings_from_melissa_public_advisory():
    ww = S.parse_warnings(S.pre_text(fx("nhc_melissa_tcp_018.html")))
    kinds = [k for k, _ in ww]
    assert ("Hurricane Warning", "Jamaica") in ww
    assert kinds.count("Hurricane Watch") == 2 and "Tropical Storm Warning" in kinds
    haiti = [a for k, a in ww if k == "Hurricane Watch"][0]
    assert haiti.startswith("Southwestern peninsula of Haiti") and haiti.endswith("Port-Au-Prince"), "a wrapped bullet is joined"
    assert not any("Interests elsewhere" in a for _, a in ww), "'monitor the progress' is not a warning"


def test_a_border_named_in_another_country_is_not_that_country():
    assert not S._mentions(S.PLACES["haiti"], "The southern coast of the Dominican Republic from Punta Palenque to the border with Haiti")
    assert S._mentions(S.PLACES["haiti"], "Southwestern peninsula of Haiti from the border with the Dominican Republic to Port-Au-Prince")


# -- the rules on recorded data ------------------------------------------------------------------------------------------
def test_melissa_is_an_alert_for_haiti_not_for_springfield():
    data = S.fetch(["springfield", "haiti"], Feeds())
    assert data["nhc"]["ok"] and [s["id"] for s in data["nhc"]["storms"]] == ["al132025"], "the Pacific storm (Sonia) is not fetched"
    events = S.evaluate(data, ["springfield", "haiti"], MELISSA_NOW)
    assert [(e["place"], e["level"]) for e in events] == [("haiti", S.ALERT)]
    text = events[0]["text"]
    assert text.startswith("Storm watch: Hurricane Melissa near Haiti.")
    assert "Hurricane Watch and a Tropical Storm Warning for Southwestern peninsula of Haiti" in text
    # between forecast points (28/1800Z 18.8N 76.7W -> 29/1800Z 22.0N 73.4W) the track passes closest to western Haiti
    assert "It is forecast to pass about 130 miles from Jeremie in about 3 days" in text
    assert "Source: National Hurricane Center, advisory 18a." in text


def test_a_hurricane_warning_for_haiti_is_danger():
    tcp = fx("nhc_melissa_tcp_018.html").replace("A Hurricane Watch is in effect for", "A Hurricane Warning is in effect for")  # EDIT
    events = S.evaluate(S.fetch(["haiti"], Feeds(**{TCP: tcp})), ["haiti"], MELISSA_NOW)
    assert events[0]["level"] == S.DANGER and events[0]["text"].startswith("Danger: Hurricane Melissa near Haiti.")


def test_a_storm_forecast_far_away_is_nothing():
    track_only = Feeds(**{TCP: "<pre>no watches</pre>"})
    assert S.evaluate(S.fetch(["springfield"], track_only), ["springfield"], MELISSA_NOW) == []


def test_the_2021_haiti_earthquake_is_danger_and_its_aftershock_is_not_said_again():
    usgs = fx("usgs_haiti_20210814.geojson")
    now = HAITI_MAIN + timedelta(minutes=30)
    events = S.evaluate(S.fetch(["haiti"], Feeds(**{S.USGS_FEED: usgs})), ["haiti"], now)
    by_id = {e["key"]: e for e in events}
    assert by_id["usgs:us6000f65h"]["level"] == S.DANGER
    assert "magnitude 7.2 earthquake hit Nippes, Haiti 30 minutes ago" in by_id["usgs:us6000f65h"]["text"]
    assert by_id["usgs:us6000f65p"]["level"] == S.ALERT, "the M5.1 near Baradères is close enough on its own"
    assert not any("Sandwich" in e["text"] or "Atlantic Ridge" in e["text"] for e in events)
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: usgs})), now)
    assert [e["key"] for e in give if e["source"] == "usgs"] == ["usgs:us6000f65h"], "the aftershock in the same 12 h is not a second warning"


def test_an_old_quake_is_not_news_but_a_question_still_lists_it():
    usgs = fx("usgs_haiti_20210814.geojson")
    later = HAITI_MAIN + timedelta(hours=7)
    data = S.fetch(["haiti"], Feeds(**{S.USGS_FEED: usgs}))
    assert [e for e in S.evaluate(data, ["haiti"], later) if e["source"] == "usgs"] == []
    assert any(e["key"] == "usgs:us6000f65h" for e in S.evaluate(data, ["haiti"], later, quake_age_h=24))


def test_nws_alerts_for_springfield_by_county_code():
    body, sent = nws_with()
    events = S.evaluate(S.fetch(["springfield"], Feeds(**{S.NWS_AREA.format(area="VA"): body})), ["springfield"], sent)
    assert [(e["key"], e["level"]) for e in events if e["source"] == "nws"] == [("nws:springfield:high wind warning", S.ALERT)]
    assert "the National Weather Service has a High Wind Warning for Springfield until" in events[0]["text"]
    body, sent = nws_with(event="Tornado Warning")
    assert S.evaluate(S.fetch(["springfield"], Feeds(**{S.NWS_AREA.format(area="VA"): body})), ["springfield"], sent)[0]["level"] == S.DANGER


@pytest.mark.parametrize("kw", [{"same": ("051700",)}, {"event": "Winter Storm Watch"}, {"event": "Freeze Watch"},
                                {"message": "Cancel"}, {"offset_h": -1.0}])
def test_nws_alerts_that_are_not_his_warnings(kw):
    body, sent = nws_with(**kw)
    events = S.evaluate(S.fetch(["springfield"], Feeds(**{S.NWS_AREA.format(area="VA"): body})), ["springfield"], sent)
    assert [e for e in events if e["source"] == "nws"] == [], kw


def test_nws_polygon_covers_a_place():
    d = json.loads(fx("nws_us_severe_sample_20261116.json"))
    flood = next(x for x in d["features"] if x["properties"]["event"] == "Flood Warning")
    ring = flood["geometry"]["coordinates"][0]
    lat, lon = sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring)
    assert S._inside(lat, lon, flood["geometry"]) and not S._inside(lat + 5, lon, flood["geometry"])


def test_the_weather_service_is_asked_for_the_state_not_his_position():
    feeds = Feeds()
    S.fetch(["springfield", "haiti"], feeds)
    nws = [u for u in feeds.asked if "weather.gov" in u]
    assert nws == ["https://api.weather.gov/alerts/active?area=VA"]
    assert not any("37.0" in u or "76.3" in u or "62701" in u for u in feeds.asked)


def test_only_the_three_public_hosts():
    with pytest.raises(RuntimeError, match="live Storm Watch feed"):
        S._http(S.USGS_FEED)        # conftest: tests never fetch live
    for url in ("http://earthquake.usgs.gov/x", "https://evil.example/x", "https://api.weather.gov.evil.example/x"):
        with pytest.raises(ValueError, match="blocked host"):
            S.check_url(url)
    for url in (S.NHC_CURRENT, S.USGS_FEED, S.NWS_AREA.format(area="VA"), TCM):
        assert S.check_url(url) == url


# -- no repeat spam, escalation, a source down -----------------------------------------------------------------------
def test_the_same_storm_is_said_once_and_again_only_when_it_becomes_danger(pc):
    data = S.fetch(["haiti"], Feeds())
    give, _ = S.pending(["haiti"], data, MELISSA_NOW)
    assert [e["level"] for e in give] == [S.ALERT]
    S.mark(give[0], MELISSA_NOW)
    for minutes in (5, 10, 60):
        assert S.pending(["haiti"], data, MELISSA_NOW + timedelta(minutes=minutes))[0] == []
    tcp = fx("nhc_melissa_tcp_018.html").replace("A Hurricane Watch is in effect for", "A Hurricane Warning is in effect for")
    S._text_cache.clear()          # the same advisory url and stamp is cached in production; this one was edited
    worse = S.fetch(["haiti"], Feeds(**{TCP: tcp}))
    give, _ = S.pending(["haiti"], worse, MELISSA_NOW + timedelta(hours=2))
    assert [e["level"] for e in give] == [S.DANGER]
    S.mark(give[0], MELISSA_NOW + timedelta(hours=2))
    assert S.pending(["haiti"], worse, MELISSA_NOW + timedelta(hours=3))[0] == []
    S._text_cache.clear()
    data = S.fetch(["haiti"], Feeds())
    assert S.pending(["haiti"], data, MELISSA_NOW + timedelta(hours=4))[0] == [], "back down to a watch is not news"


def test_a_failed_source_never_forgets_and_is_said_once_after_three_hours(pc):
    data = S.fetch(["haiti"], Feeds())
    give, _ = S.pending(["haiti"], data, MELISSA_NOW)
    S.mark(give[0], MELISSA_NOW)
    down = S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: TimeoutError("timed out")}))
    assert down["nhc"]["ok"] is False and "TimeoutError" in down["nhc"]["error"]
    assert S.pending(["haiti"], down, MELISSA_NOW + timedelta(hours=1)) == ([], [])
    _, notices = S.pending(["haiti"], down, MELISSA_NOW + timedelta(hours=4))
    assert notices == ["Storm watch hasn't been able to reach the National Hurricane Center for 3 hours, so I can't warn you "
                       "about hurricanes until it's back."]
    assert S.pending(["haiti"], down, MELISSA_NOW + timedelta(hours=5))[1] == [], "said once"
    give, notices = S.pending(["haiti"], data, MELISSA_NOW + timedelta(hours=6))
    assert give == [], "the outage did not make Melissa new again"
    assert notices == ["Storm watch can reach the National Hurricane Center again."]


def test_an_nws_alert_that_ends_is_withdrawn_and_forgotten(pc, monkeypatch):
    withdrawn = []
    monkeypatch.setattr(attention, "withdraw", lambda key: withdrawn.append(key) or True)
    body, sent = nws_with()
    on = S.fetch(["springfield"], Feeds(**{S.NWS_AREA.format(area="VA"): body}))
    give, _ = S.pending(["springfield"], on, sent)
    S.mark(give[0], sent)
    off = S.fetch(["springfield"], Feeds())
    S.pending(["springfield"], off, sent + timedelta(hours=1))
    assert withdrawn == [] and "nws:springfield:high wind warning" in storage.load(S._state_name(), {})["said"], "one empty poll is a blip"
    S.pending(["springfield"], off, sent + timedelta(hours=1, minutes=25))
    assert "storm:nws:springfield:high wind warning:alert" in withdrawn, "a held warning that ended is not said later"
    assert "nws:springfield:high wind warning" not in storage.load(S._state_name(), {})["said"]


# -- delivery: what is said, what is pushed, quiet hours ---------------------------------------------------------------
def test_alert_is_spoken_by_attention_and_pushed_normally(pc, out):
    said, pushed = out
    ev = S.evaluate(S.fetch(["haiti"], Feeds()), ["haiti"], MELISSA_NOW)[0]
    S.speak(ev)
    S.ping(ev)
    assert said[0]["urgency"] == attention.HIGH and said[0]["sit"] is None, "he is away: attention holds it for his return"
    assert pushed == [{"title": "Storm Watch", "text": ev["text"], "category": "weather", "urgent": False}]


def test_quiet_hours_hold_an_alert_but_danger_gets_through(pc, out, monkeypatch):
    said, pushed = out
    monkeypatch.setattr(S, "quiet_now", lambda uid=None: True)
    ev = S.evaluate(S.fetch(["haiti"], Feeds()), ["haiti"], MELISSA_NOW)[0]
    assert S.speak(ev).startswith("quiet hours: held")
    assert said[-1]["urgency"] == attention.LOW, "an alert in quiet hours waits until he is back"
    danger = {**ev, "level": S.DANGER, "text": "Danger: test line."}
    assert S.speak(danger) == "said it out loud"
    assert said[-1]["urgency"] == attention.CRITICAL and said[-1]["sit"]["present"] is True, "said even to an empty room"
    assert "urgent" in S.ping(danger) and pushed[-1]["urgent"] is True and pushed[-1]["title"].startswith("DANGER")


def test_the_laptop_pings_and_the_pc_speaks(out, monkeypatch):
    ev = S.evaluate(S.fetch(["haiti"], Feeds()), ["haiti"], MELISSA_NOW)[0]
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setenv("JARVIS_ROLE", "desk")
    monkeypatch.setenv("JARVIS_SERVER_URL", "http://laptop.invalid:8765")
    monkeypatch.setenv("JARVIS_SERVER_TOKEN", "t")
    assert P._ping({"do": "ping"}, {"alert": ev}) == (P.SKIPPED, "the laptop sends the phone alert")
    assert P._warn({"do": "warn"}, {"alert": ev})[0] == P.DONE
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setenv("JARVIS_ROLE", "server")
    assert P._warn({"do": "warn"}, {"alert": ev}) == (P.SKIPPED, "the PC says warnings out loud")
    assert P._ping({"do": "ping"}, {"alert": ev})[0] == P.DONE
    assert S._state_name() == "stormwatch-server.json", "each machine writes its own state file"


# -- the protocol: built in, approved by his request, withdrawable, voice ----------------------------------------------
def test_storm_watch_is_a_builtin_approved_protocol():
    proto = P.find("storm watch")
    assert proto and P.approved(proto) and P.storm_places(proto) == ["springfield", "haiti"]
    text = P.describe(proto)
    assert "warn you out loud and ping your phone" in text and "forecast near Springfield, Illinois or Haiti" in text
    assert "approved to run on its own" in text
    assert "storm watch (automatic, approved)" in P.listing()


def test_storm_watch_runs_once_per_new_warning_and_records_it(pc, out):
    said, pushed = out
    results = P.outside_tick(get=Feeds(), now=MELISSA_NOW, force=True)
    assert len(results) == 1 and [r["status"] for r in results[0]["steps"]] == [P.DONE, P.DONE]
    assert len(said) == 1 and said[0]["text"].startswith("Storm watch: Hurricane Melissa near Haiti")
    assert len(pushed) == 1 and pushed[0]["category"] == "weather"
    assert P.outside_tick(get=Feeds(), now=MELISSA_NOW + timedelta(minutes=5), force=True) == []
    assert len(said) == 1 and len(pushed) == 1, "no repeat"
    how = P.handle("how did the storm watch protocol go")
    assert how.startswith("Storm watch protocol (Hurricane Melissa near Haiti): both steps done.")


def test_a_withdrawn_storm_watch_never_runs(pc, out):
    assert P.handle("withdraw approval for the storm watch protocol").startswith("Done: the storm watch protocol won't run on its own")
    assert P.outside_tick(get=Feeds(), now=MELISSA_NOW, force=True) == []
    assert out == ([], [])
    assert P.handle("approve the storm watch protocol").startswith("Approved.")
    assert len(P.outside_tick(get=Feeds(), now=MELISSA_NOW, force=True)) == 1


def test_the_switch_and_the_interval(pc, out, monkeypatch):
    monkeypatch.setenv("JARVIS_STORMWATCH", "off")
    assert P.outside_tick(get=Feeds(), now=MELISSA_NOW, force=True) == []
    monkeypatch.setenv("JARVIS_STORMWATCH", "on")
    feeds = Feeds()
    P.outside_tick(get=feeds, now=MELISSA_NOW)
    asked = len(feeds.asked)
    assert asked and P.outside_tick(get=feeds, now=MELISSA_NOW) == [] and len(feeds.asked) == asked, "once per 5 minutes"


def test_a_household_member_has_no_storm_watch(pc):
    from core import users
    wife = users.add("Partner", {"en": "ma'am"})
    with users.acting_as(wife):
        assert P.find("storm watch") is None and P.handle("storm watch") is None


@pytest.mark.parametrize("said", ["storm watch", "Jarvis, storm watch.", "check storm watch", "storm watch status",
                                  "storm watch protocol", "run the storm watch protocol", "what's on storm watch?"])
def test_asking_gives_a_live_report_with_sources(pc, out, said, monkeypatch):
    monkeypatch.setattr(S, "latest", lambda places, get=None, max_age=0: S.fetch(places, Feeds()))
    monkeypatch.setattr(S, "_now", lambda: MELISSA_NOW)
    reply = P.handle(said)
    assert reply and "Storm watch" in reply and "Hurricane Melissa" in reply, (said, reply)
    assert out == ([], []), "a question is answered, not warned and pushed"


def test_all_clear_says_its_sources_and_a_failure_is_never_all_clear(pc, monkeypatch):
    quiet = {"activeStorms": []}
    feeds = Feeds(**{S.NHC_CURRENT: json.dumps(quiet)})
    monkeypatch.setattr(S, "latest", lambda places, get=None, max_age=0: S.fetch(places, feeds))
    reply = P.handle("storm watch")
    assert reply.startswith("Storm watch: all clear for Springfield, Illinois and Haiti right now")
    assert "the National Hurricane Center" in reply and "the US Geological Survey" in reply and "not Haiti" in reply
    broken = Feeds(**{S.NHC_CURRENT: TimeoutError("timed out")})
    monkeypatch.setattr(S, "latest", lambda places, get=None, max_age=0: S.fetch(places, broken))
    reply = P.handle("storm watch")
    assert "hurricanes or tropical storms" not in reply and "I couldn't reach the National Hurricane Center" in reply


def test_not_storm_watch_questions():
    for said in ("I like to storm watch from the porch at night", "the storm watching was fun", "what is a storm watch"):
        assert P.handle(said) is None, said


# -- the globe ---------------------------------------------------------------------------------------------------------
def test_globe_layer_and_feed(pc, monkeypatch):
    from core import worldintel
    layer = next(l for l in worldintel.LAYERS if l["id"] == "stormwatch")
    assert layer["feed"] == "stormwatch" and layer["gev"] == ["weather-cyclones"], "NHC tracks drawn by the globe itself"
    monkeypatch.setattr(S, "latest", lambda places, get=None, max_age=0: S.fetch(places, Feeds()))
    monkeypatch.setattr(S, "_now", lambda: MELISSA_NOW)
    feed = S.globe_feed(["springfield", "haiti"])
    kinds = [i["kind"] for i in feed["items"]]
    assert kinds[:2] == ["pinned", "pinned"] and "storm" in kinds and feed["count"] == 1 and feed["ok"]
    storm = next(i for i in feed["items"] if i["kind"] == "storm")
    assert storm["feed"] == "stormwatch" and storm["lat"] == 16.6 and storm["provider"] == "US National Hurricane Center"
    page = (Path(__file__).parent.parent / "app" / "world.html").read_text(encoding="utf-8")
    assert '"stormwatch"].forEach' in page and 'k.startsWith("sw")' in page and "Storm Watch" in page


def test_globe_voice_preset():
    from core import worldintel
    assert "storm" in worldintel.PRESETS and worldintel.PRESETS["storm"]["layers"] == ["stormwatch"]
    assert any(k == "storm" and r.fullmatch("show me the storm watch map") for k, r in worldintel._PRESET_RE)


# -- review 2026-11-16: the fixes, each with the scenario the reviewer found -------------------------------------------
def test_a_new_nws_warning_after_the_last_one_ended_is_said_again(pc):
    body, sent = nws_with(event="Tornado Warning", offset_h=0.5, onset_h=0)
    area = S.NWS_AREA.format(area="VA")
    give, _ = S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent)
    S.mark(give[0], sent)
    S.pending(["springfield"], S.fetch(["springfield"], Feeds()), sent + timedelta(hours=1))           # it ended: empty list
    body2, _ = nws_with(event="Tornado Warning", offset_h=4, onset_h=1.9)
    give, _ = S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body2})), sent + timedelta(hours=2))
    assert [(e["key"], e["level"]) for e in give] == [("nws:springfield:tornado warning", S.DANGER)], "a second tornado is new"


def test_an_nws_blip_or_an_update_is_not_said_twice(pc):
    area = S.NWS_AREA.format(area="VA")
    body, sent = nws_with(event="Tornado Warning", offset_h=1, onset_h=0)
    give, _ = S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent)
    S.mark(give[0], sent)
    S.pending(["springfield"], S.fetch(["springfield"], Feeds()), sent + timedelta(minutes=5))          # one empty poll
    assert S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent + timedelta(minutes=10))[0] == []
    update, _ = nws_with(event="Tornado Warning", offset_h=1.5, onset_h=0, message="Update")    # same onset, longer
    assert S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: update})), sent + timedelta(minutes=15))[0] == []


def test_a_warning_issued_before_it_begins_is_said_once(pc):
    """The recorded High Wind Warning begins (onset) the next day, after its message expires: never re-said each poll."""
    area = S.NWS_AREA.format(area="VA")
    body, sent = nws_with()
    give, _ = S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent)
    S.mark(give[0], sent)
    for minutes in (5, 10, 15):
        assert S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent + timedelta(minutes=minutes))[0] == []


def _quake(mag, minutes, dlat=0.0, qid="x"):
    """The recorded M7.2 Haiti row with magnitude, time, position and id EDITED (said here)."""
    d = json.loads(fx("usgs_haiti_20210814.geojson"))
    f = copy.deepcopy(next(x for x in d["features"] if x["id"] == "us6000f65h"))
    f["id"] = qid
    f["properties"]["mag"] = mag
    f["properties"]["time"] = int((HAITI_MAIN + timedelta(minutes=minutes)).timestamp() * 1000)
    f["geometry"]["coordinates"][1] += dlat
    return f


def _usgs(*features):
    return json.dumps({"type": "FeatureCollection", "features": list(features)})


def test_a_clearly_stronger_quake_is_not_folded_into_a_weaker_one(pc):
    first = _quake(5.0, 0, qid="a")
    t0 = HAITI_MAIN + timedelta(minutes=5)
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: _usgs(first)})), t0)
    assert [e["key"] for e in give if e["source"] == "usgs"] == ["usgs:a"]
    S.mark(give[-1], t0)
    small = _quake(5.2, 60, qid="b")
    big = _quake(6.4, 180, dlat=0.5, qid="c")
    t1 = HAITI_MAIN + timedelta(minutes=185)
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: _usgs(first, small, big)})), t1)
    assert [e["key"] for e in give if e["source"] == "usgs"] == ["usgs:c"], "the M6.4 is said, the M5.2 aftershock is not"


def test_in_one_check_the_strongest_quake_is_the_one_said(pc):
    feed = _usgs(_quake(5.1, 0, qid="small"), _quake(6.4, 1, dlat=0.3, qid="big"))
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: feed})), HAITI_MAIN + timedelta(minutes=10))
    assert [e["key"] for e in give if e["source"] == "usgs"] == ["usgs:big"]


def test_a_storm_that_cannot_be_read_never_silences_the_rest_and_is_never_all_clear(pc, monkeypatch):
    cur = json.loads(fx("nhc_current_20251026.json"))
    melissa = cur["activeStorms"][0]
    melissa["forecastAdvisory"]["issuance"] = "2025-10-25T21:00:00"            # EDIT: no zone, and no advisory text
    feeds = Feeds(**{S.NHC_CURRENT: json.dumps(cur), TCM: "<pre>garbled</pre>",
                     S.USGS_FEED: _usgs(_quake(7.2, 0, qid="q"))})
    now = HAITI_MAIN + timedelta(minutes=5)
    monkeypatch.setattr(S, "_now", lambda: now)
    data = S.fetch(["haiti"], feeds)
    events = S.evaluate(data, ["haiti"], now)
    assert any(e["key"] == "usgs:q" for e in events), "the quake is still found"
    assert data["nhc"]["unread"], "the storm without a readable forecast is flagged"
    monkeypatch.setattr(S, "latest", lambda places, get=None, max_age=0: data)
    reply = S.report(["haiti"])
    assert "couldn't read the Hurricane Center's forecast for Hurricane Melissa" in reply and "no hurricanes" not in reply


def test_a_crash_in_one_sources_rules_makes_it_down_not_clear(pc, monkeypatch):
    monkeypatch.setattr(S, "quake_events", lambda *a, **k: 1 / 0)
    data = S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps({"activeStorms": []})}))
    S.evaluate(data, ["haiti"], MELISSA_NOW)
    assert data["usgs"]["ok"] is False and "ZeroDivisionError" in data["usgs"]["error"]


def test_the_track_between_forecast_points_counts():
    a = {"lat": 35.8, "lon": -93.6, "kt": 90, "when": MELISSA_NOW, "kind": "now"}
    b = {"lat": 43.8, "lon": -85.7, "kt": 90, "when": MELISSA_NOW + timedelta(hours=12), "kind": "forecast"}
    place = S.PLACES["springfield"]
    assert min(S.nearest(place, p["lat"], p["lon"])[0] for p in (a, b)) > S.STORM_KM, "both points are far"
    assert min(S.nearest(place, p["lat"], p["lon"])[0] for p in S._along([a, b])) < 60, "the line between them crosses Springfield"


def test_a_heading_the_parser_does_not_know_ends_the_last_one():
    text = ("SUMMARY OF WATCHES AND WARNINGS IN EFFECT:\n\nA Hurricane Warning is in effect for...\n* Jamaica\n\n"
            "The Tropical Storm Watch remains in effect for...\n* Haiti\n")
    assert S.parse_warnings(text) == [("Hurricane Warning", "Jamaica")]
    assert not S._mentions(S.PLACES["haiti"], "Dominican Republic from Cabo Engano to the Haiti border")


def test_in_quiet_hours_he_hears_it_if_he_is_at_the_pc(pc, out, monkeypatch):
    said, _ = out
    monkeypatch.setattr(S, "quiet_now", lambda uid=None: True)
    monkeypatch.setattr(attention, "situation", lambda: {"present": True, "gaming": "", "idle": 5})
    ev = S.evaluate(S.fetch(["haiti"], Feeds()), ["haiti"], MELISSA_NOW)[0]
    S.speak(ev)
    assert said[-1]["urgency"] == attention.HIGH, "awake at the desk at 23:00: said, not held until a return that never comes"


def test_a_warning_whose_steps_all_failed_is_tried_again_then_given_up(pc, out, monkeypatch):
    said, pushed = out

    def broken(step, ctx):
        raise RuntimeError("speaker gone")
    monkeypatch.setattr(P, "ACTIONS", {**P.ACTIONS, "warn": broken, "ping": broken})
    for i in range(1, 4):
        results = P.outside_tick(get=Feeds(), now=MELISSA_NOW + timedelta(minutes=5 * i), force=True)
        assert len(results) == 1 and all(r["status"] == P.FAILED for r in results[0]["steps"]), i
    assert P.outside_tick(get=Feeds(), now=MELISSA_NOW + timedelta(minutes=20), force=True) == [], "3 tries, then marked"


def test_a_redirect_off_the_three_hosts_is_refused():
    handler = S._AllowedRedirects()
    import urllib.request
    req = urllib.request.Request(S.USGS_FEED)
    with pytest.raises(ValueError, match="blocked host"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://elsewhere.example/feed.json")
    assert handler.redirect_request(req, None, 302, "Found", {}, S.USGS_FEED.replace("4.5_day", "all_day")) is not None


def test_globe_never_says_all_clear_on_a_failed_feed():
    page = (Path(__file__).parent.parent / "app" / "world.html").read_text(encoding="utf-8")
    assert 'sw && sw.ok !== false && sw.items' in page and "part of the check failed" in page


# -- review round 2 -------------------------------------------------------------------------------------------------
def test_a_source_whose_rules_crash_keeps_what_was_said(pc, monkeypatch):
    area = S.NWS_AREA.format(area="VA")
    body, sent = nws_with(event="Tornado Warning", offset_h=3, onset_h=0)
    give, _ = S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent)
    S.mark(give[0], sent)
    real = S.alert_events
    monkeypatch.setattr(S, "alert_events", lambda *a, **k: 1 / 0)
    for minutes in (5, 30, 60):
        S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent + timedelta(minutes=minutes))
    monkeypatch.setattr(S, "alert_events", real)
    assert S.evaluate(S.fetch(["springfield"], Feeds(**{area: body})), ["springfield"], sent + timedelta(minutes=65)), "still active"
    assert S.pending(["springfield"], S.fetch(["springfield"], Feeds(**{area: body})), sent + timedelta(minutes=65))[0] == [], \
        "unreadable data is a source down, not the alert ending"


def test_an_escalation_gets_its_own_tries(pc, out, monkeypatch):
    def broken(step, ctx):
        raise RuntimeError("speaker gone")
    monkeypatch.setattr(P, "ACTIONS", {**P.ACTIONS, "warn": broken, "ping": broken})
    for i in (1, 2):
        P.outside_tick(get=Feeds(), now=MELISSA_NOW + timedelta(minutes=5 * i), force=True)
    S._text_cache.clear()
    tcp = fx("nhc_melissa_tcp_018.html").replace("A Hurricane Watch is in effect for", "A Hurricane Warning is in effect for")
    P.outside_tick(get=Feeds(**{TCP: tcp}), now=MELISSA_NOW + timedelta(minutes=15), force=True)   # DANGER, voice still broken
    monkeypatch.setattr(P, "ACTIONS", {**P.ACTIONS, "warn": P._warn, "ping": P._ping})
    results = P.outside_tick(get=Feeds(**{TCP: tcp}), now=MELISSA_NOW + timedelta(minutes=20), force=True)
    assert [r["about"] for r in results] == ["Hurricane Melissa near Haiti"] and out[0][-1]["urgency"] == attention.CRITICAL
    assert P._storm_tries == {}, "nothing left waiting"


def test_readers_never_share_one_evaluation(pc):
    feeds = Feeds()
    S.check(["haiti"], feeds)
    a = S.latest(["haiti"])
    a["nhc"]["unread"] = ["scribbled"]
    assert "unread" not in S.latest(["haiti"])["nhc"], "each reader gets its own copy"


def test_an_unreadable_forecast_is_told_once(pc):
    cur = json.loads(fx("nhc_current_20251026.json"))
    data = S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps(cur), TCM: "<pre>garbled</pre>"}))
    _, notices = S.pending(["haiti"], data, MELISSA_NOW)
    assert notices == ["Storm watch can't read the Hurricane Center's forecast for Hurricane Melissa, so I'm only watching "
                       "where it is now, not where it's heading."]
    assert S.pending(["haiti"], data, MELISSA_NOW + timedelta(minutes=5))[1] == []


def test_a_folded_aftershock_revised_upward_is_judged_again(pc):
    t = HAITI_MAIN + timedelta(minutes=30)
    feed = _usgs(_quake(5.0, 0, qid="a"), _quake(5.1, 10, qid="b"))
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: feed})), t)
    for e in give:
        S.mark(e, t)
    assert [e["key"] for e in give if e["source"] == "usgs"] == ["usgs:b"]
    revised = _usgs(_quake(5.0, 0, qid="a"), _quake(5.1, 10, qid="b"), _quake(5.0, 5, qid="c"))
    S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: revised})), t + timedelta(minutes=5))
    bigger = _usgs(_quake(5.0, 0, qid="a"), _quake(5.1, 10, qid="b"), _quake(5.9, 5, qid="c"))
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.USGS_FEED: bigger})), t + timedelta(minutes=10))
    assert [e["key"] for e in give if e["source"] == "usgs"] == ["usgs:c"]


# -- review round 3 -------------------------------------------------------------------------------------------------
def test_a_held_storm_warning_is_withdrawn_when_the_storm_is_no_longer_near(pc, monkeypatch):
    withdrawn = []
    monkeypatch.setattr(attention, "withdraw", lambda key: withdrawn.append(key) or True)
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds()), MELISSA_NOW)
    S.mark(give[0], MELISSA_NOW)
    gone = S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps({"activeStorms": []})}))
    S.pending(["haiti"], gone, MELISSA_NOW + timedelta(hours=1))
    assert "storm:nhc:al132025:haiti:alert" in withdrawn, "the 'while you were away' summary won't name a storm that left"
    assert "nhc:al132025:haiti" in storage.load(S._state_name(), {})["said"], "but it is remembered (it may turn back)"


def test_an_unreadable_storm_is_told_once_even_when_the_reason_changes(pc):
    cur = json.loads(fx("nhc_current_20251026.json"))
    garbled = S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps(cur), TCM: "<pre>garbled</pre>"}))
    assert len(S.pending(["haiti"], garbled, MELISSA_NOW)[1]) == 1
    S._text_cache.clear()
    stale = S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps(cur)}))
    assert S.pending(["haiti"], stale, MELISSA_NOW + timedelta(days=4))[1] == [], "now stale instead of garbled: same storm"
    assert S.pending(["haiti"], stale, MELISSA_NOW + timedelta(days=12))[1] == [], "still unread after a week: not re-told"


def test_a_storm_that_drifts_out_and_back_while_he_is_away_is_still_told(pc, monkeypatch):
    held = {"storm:nhc:al132025:haiti:alert"}
    monkeypatch.setattr(attention, "withdraw", lambda key: key in held and not held.discard(key))
    data = S.fetch(["haiti"], Feeds())
    give, _ = S.pending(["haiti"], data, MELISSA_NOW)
    S.mark(give[0], MELISSA_NOW)                                     # said to attention, which held it (he is away)
    S._text_cache.clear()
    S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps({"activeStorms": []})})),
              MELISSA_NOW + timedelta(hours=1))                      # out of range for one check: the held line is withdrawn
    give, _ = S.pending(["haiti"], S.fetch(["haiti"], Feeds()), MELISSA_NOW + timedelta(hours=2))
    assert [e["key"] for e in give] == ["nhc:al132025:haiti"], "he never heard it: back in range, it is said again"


def test_a_storm_he_already_heard_is_not_repeated_after_drifting_out_and_back(pc, monkeypatch):
    monkeypatch.setattr(attention, "withdraw", lambda key: False)    # nothing held: it was said out loud
    data = S.fetch(["haiti"], Feeds())
    give, _ = S.pending(["haiti"], data, MELISSA_NOW)
    S.mark(give[0], MELISSA_NOW)
    S._text_cache.clear()
    S.pending(["haiti"], S.fetch(["haiti"], Feeds(**{S.NHC_CURRENT: json.dumps({"activeStorms": []})})),
              MELISSA_NOW + timedelta(hours=1))
    assert S.pending(["haiti"], S.fetch(["haiti"], Feeds()), MELISSA_NOW + timedelta(hours=2))[0] == []
