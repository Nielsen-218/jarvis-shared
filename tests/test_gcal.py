"""Google Calendar skills against a fake Google (no network, no real token)."""
import json
import time
import urllib.parse
from datetime import datetime

import httpx
import pytest

from core import gcal, ownertime, storage
from tools import gcalendar


class FakeGoogle:
    def __init__(self):
        self.events = {}
        self.calls = []
        self.refreshes = 0
        self.next_id = 1
        self.fail = None

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        if url == gcal.TOKEN_URL:
            self.refreshes += 1
            return httpx.Response(200, json={"access_token": f"at{self.refreshes}", "expires_in": 3600})
        return httpx.Response(200, json={})

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):  # noqa: A002
        self.calls.append((method, url, params or json, headers))
        if self.fail:
            return httpx.Response(self.fail, json={})
        path = url[len(gcal.API):]
        base = "/calendars/primary/events"
        if method == "GET":
            lo, hi = params["timeMin"][:16], params["timeMax"][:16]
            items = [e for e in self.events.values() if lo <= self._start(e) < hi]
            if params.get("q"):
                items = [e for e in items if params["q"].lower() in e["summary"].lower()]
            return httpx.Response(200, json={"items": sorted(items, key=self._start)})
        if method == "POST" and path == base:
            ev = {**json, "id": f"ev{self.next_id}"}
            self.next_id += 1
            self.events[ev["id"]] = ev
            return httpx.Response(200, json=ev)
        eid = urllib.parse.unquote(path[len(base) + 1:])
        if eid not in self.events:
            return httpx.Response(404, json={})
        if method == "PATCH":
            self.events[eid].update(json)
            return httpx.Response(200, json=self.events[eid])
        if method == "DELETE":
            del self.events[eid]
            return httpx.Response(204)
        raise AssertionError(method)

    @staticmethod
    def _start(e):
        s = e["start"]
        return s.get("dateTime", s.get("date", ""))[:16]


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(gcal.httpx, "post", fake.post)
    monkeypatch.setattr(gcal.httpx, "request", fake.request)
    monkeypatch.setattr(ownertime, "now", lambda: datetime(2026, 11, 20, 9, 0))
    storage.save(gcal.TOKEN_FILE, {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt",
                                   "access_token": "", "expires_at": 0})
    return fake


def _add(fake, title, start, end=None):
    ev = {"summary": title, "start": {"dateTime": start, "timeZone": "America/New_York"},
          "end": {"dateTime": end or start, "timeZone": "America/New_York"}, "id": f"ev{fake.next_id}"}
    fake.next_id += 1
    fake.events[ev["id"]] = ev
    return ev


def test_not_connected_says_how_to_connect(monkeypatch):
    storage.save(gcal.TOKEN_FILE, {})
    out = gcalendar.calendar_events()
    assert "isn't connected" in out and "tap Calendar" in out
    assert gcalendar.today_summary() == ""


def test_today_reads_events_and_refreshes_token_once(google):
    _add(google, "Dentist", "2026-11-20T15:00:00", "2026-11-20T16:00:00")
    _add(google, "Tomorrow thing", "2026-11-21T10:00:00")
    out = gcalendar.calendar_events()
    assert out == "Your calendar, today: Dentist, 3 PM to 4 PM."
    gcalendar.calendar_events()
    assert google.refreshes == 1  # cached access token reused
    assert google.calls[-1][3]["Authorization"] == "Bearer at1"


def test_empty_day_is_said_plainly(google):
    assert gcalendar.calendar_events("2026-11-22") == "Nothing on your calendar for Sunday November 22."
    assert gcalendar.calendar_events() == "Nothing on your calendar today."


def test_query_searches_ahead(google):
    _add(google, "Dentist", "2026-12-15T09:30:00", "2026-12-15T10:00:00")
    out = gcalendar.calendar_events(query="dentist")
    assert "Dentist, Tuesday December 15 at 9:30 AM to 10 AM" in out


def test_add_event_defaults_to_one_hour_in_owner_zone(google):
    out = gcalendar.add_calendar_event("Gym", "2026-11-21T17:00")
    ev = next(iter(google.events.values()))
    assert ev["start"] == {"dateTime": "2026-11-21T17:00:00", "timeZone": "America/New_York"}
    assert ev["end"]["dateTime"] == "2026-11-21T18:00:00"
    assert out == "Added to your calendar: Gym, Saturday November 21 at 5 PM to 6 PM."


def test_add_all_day_and_past_time(google):
    out = gcalendar.add_calendar_event("Mom's birthday", "2026-12-02")
    ev = next(iter(google.events.values()))
    assert ev["start"] == {"date": "2026-12-02"} and ev["end"] == {"date": "2026-12-03"}
    assert "all day" in out
    assert "already passed" in gcalendar.add_calendar_event("Late", "2026-11-19T08:00")
    assert len(google.events) == 1


def test_move_keeps_length(google):
    _add(google, "Dentist", "2026-11-24T15:00:00", "2026-11-24T15:30:00")
    out = gcalendar.move_calendar_event("dentist", "2026-11-26T16:00")
    ev = google.events["ev1"]
    assert ev["start"]["dateTime"] == "2026-11-26T16:00:00" and ev["end"]["dateTime"] == "2026-11-26T16:30:00"
    assert out.startswith("Moved: Dentist, Thursday November 26 at 4 PM to 4:30 PM")


def test_cancel_asks_when_several_match_and_deletes_nothing(google):
    _add(google, "Gym", "2026-11-21T17:00:00")
    _add(google, "Gym", "2026-11-23T17:00:00")
    out = gcalendar.cancel_calendar_event("gym")
    assert "Which one?" in out and len(google.events) == 2
    out = gcalendar.cancel_calendar_event("gym", "2026-11-23T17:00")
    assert out.startswith("Cancelled: Gym, Monday November 23") and list(google.events) == ["ev1"]


def test_unknown_event(google):
    assert "don't see anything called 'piano'" in gcalendar.cancel_calendar_event("piano")


def test_google_errors_are_spoken_not_raised(google):
    google.fail = 403
    assert "isn't allowed" in gcalendar.calendar_events()
    google.fail = 500
    assert "nothing was changed" in gcalendar.add_calendar_event("X", "2026-11-21T10:00")


def test_revoked_token_says_rerun_setup(google, monkeypatch):
    monkeypatch.setattr(gcal.httpx, "post", lambda *a, **k: httpx.Response(400, json={"error": "invalid_grant"}))
    assert "signed Jarvis out" in gcalendar.calendar_events()


def test_briefing_includes_calendar(google):
    _add(google, "Standup", "2026-11-20T10:00:00", "2026-11-20T10:15:00")
    assert gcalendar.today_summary() == "Calendar today: Standup, 10 AM to 10:15 AM"
    from tools import proactive
    assert "Calendar today: Standup" in proactive.briefing_info()


def test_load_client_reads_desktop_json(tmp_path):
    p = tmp_path / "client.json"
    p.write_text(json.dumps({"installed": {"client_id": "a", "client_secret": "b"}}))
    assert gcal.load_client(str(p)) == {"client_id": "a", "client_secret": "b"}
    p.write_text("{}")
    with pytest.raises(gcal.CalendarError):
        gcal.load_client(str(p))


def test_authorize_loopback_flow(monkeypatch):
    """The browser hits our loopback with the code; we exchange it with the PKCE verifier and save the token."""
    posted = {}

    def fake_post(url, data=None, timeout=None):
        posted.update(data)
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

    def browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        redirect, state = q["redirect_uri"][0], q["state"][0]
        assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
        import threading
        threading.Thread(target=lambda: httpx.get(f"{redirect}/?code=abc&state={state}"), daemon=True).start()

    monkeypatch.setattr(gcal.httpx, "post", fake_post)
    rec = gcal.authorize({"client_id": "cid", "client_secret": "sec"}, open_browser=browser, timeout=10)
    assert posted["code"] == "abc" and posted["code_verifier"] and posted["grant_type"] == "authorization_code"
    assert rec["refresh_token"] == "rt" and gcal.connected()
    assert storage.load(gcal.TOKEN_FILE, {})["client_id"] == "cid"
    assert rec["expires_at"] > time.time()


# -- review round 1 regressions ------------------------------------------------------------
@pytest.mark.parametrize("no_tzdb", [False, True])
def test_offset_is_the_one_at_that_date(monkeypatch, no_tzdb):
    if no_tzdb:
        monkeypatch.setattr(ownertime, "_zone", lambda name: None)
    elif ownertime._zone("America/New_York") is None:
        pytest.skip("no tz database here")
    assert gcal._rfc3339(datetime(2027, 7, 4)) == "2027-07-04T00:00:00-04:00"
    assert gcal._rfc3339(datetime(2026, 11, 20, 9)) == "2026-11-20T09:00:00-05:00"


@pytest.mark.parametrize("no_tzdb", [False, True])
def test_event_times_use_their_own_dst(monkeypatch, no_tzdb):
    if no_tzdb:
        monkeypatch.setattr(ownertime, "_zone", lambda name: None)
    elif ownertime._zone("America/New_York") is None:
        pytest.skip("no tz database here")
    summer = {"start": {"dateTime": "2027-03-20T19:00:00Z"}}
    winter = {"start": {"dateTime": "2026-12-20T20:00:00Z"}}
    assert gcal._event_start(summer)[0] == datetime(2027, 3, 20, 15, 0)
    assert gcal._event_start(winter)[0] == datetime(2026, 12, 20, 15, 0)


def test_notes_mention_never_makes_an_event_the_target(google):
    ev = _add(google, "School pickup", "2026-11-21T15:00:00")
    ev["description"] = "before dentist"

    def q_also_searches_notes(method, url, headers=None, params=None, json=None, timeout=None):  # noqa: A002
        if method == "GET" and params.get("q"):
            return httpx.Response(200, json={"items": [ev]})
        return FakeGoogle.request(google, method, url, headers, params, json, timeout)

    import core.gcal as g
    g.httpx.request = q_also_searches_notes  # monkeypatch fixture restores it
    assert "don't see anything called 'dentist'" in gcalendar.cancel_calendar_event("dentist")
    assert "ev1" in google.events


def test_yesterdays_event_is_not_a_target(google):
    _add(google, "Gym", "2026-11-19T17:00:00")
    assert "don't see anything" in gcalendar.cancel_calendar_event("gym")


def test_unreadable_google_answer_is_spoken(google, monkeypatch):
    monkeypatch.setattr(gcal.httpx, "request", lambda *a, **k: httpx.Response(200, text="<html>proxy</html>"))
    assert "couldn't read" in gcalendar.calendar_events()
    assert gcalendar.today_summary() == "Calendar today: couldn't reach Google Calendar"
    monkeypatch.setattr(gcal.httpx, "request",
                        lambda *a, **k: httpx.Response(200, json={"items": [{"summary": "x", "start": {"dateTime": 5}}]}))
    assert "couldn't read" in gcalendar.calendar_events()


def test_null_optional_args_do_not_crash(google):
    assert gcalendar.calendar_events(from_date=None, to_date=None, query=None) == "Nothing on your calendar today."


def test_multi_day_all_day_says_through(google):
    out = gcalendar.add_calendar_event("Trip", "2026-12-02", "2026-12-04")
    ev = next(iter(google.events.values()))
    assert ev["end"] == {"date": "2026-12-05"}
    assert "all day through Friday December 4" in out


def test_stray_loopback_request_does_not_end_sign_in(monkeypatch):
    monkeypatch.setattr(gcal.httpx, "post", lambda url, data=None, timeout=None: httpx.Response(
        200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}))

    def browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        redirect, state = q["redirect_uri"][0], q["state"][0]
        import threading

        def hits():
            assert httpx.get(f"{redirect}/?code=evil&state=wrong").status_code == 400
            httpx.get(f"{redirect}/?code=good&state={state}")
        threading.Thread(target=hits, daemon=True).start()

    rec = gcal.authorize({"client_id": "cid", "client_secret": "sec"}, open_browser=browser, timeout=10)
    assert rec["refresh_token"] == "rt"


def test_filler_words_do_not_block_a_title_match(google):
    _add(google, "Dentist", "2026-11-24T15:00:00", "2026-11-24T16:00:00")
    assert gcalendar.cancel_calendar_event("the dentist appointment").startswith("Cancelled: Dentist")


def test_done_is_said_even_if_the_answer_cannot_be_described(google, monkeypatch):
    monkeypatch.setattr(gcal, "describe", lambda ev, with_day=True: (_ for _ in ()).throw(gcal.CalendarError("x")))
    assert gcalendar.add_calendar_event("Gym", "2026-11-21T17:00") == "Added to your calendar: Gym."


# -- live 2026-11-20: calendar questions reach the skill, done-claims without a call are caught -------------------------
@pytest.mark.parametrize("said,first,last", [
    ("What's on my calendar today?", "2026-11-20", "2026-11-20"),
    ("Am I free on Tuesday?", "2026-11-24", "2026-11-24"),
    ("What do I have tomorrow?", "2026-11-21", "2026-11-21"),
    ("Am I busy tonight, sir?", "2026-11-20", "2026-11-20"),
    ("What's on my schedule this week?", "2026-11-20", "2026-11-22"),
    ("Do I have anything next week?", "2026-11-23", "2026-11-29"),
    ("Do I have anything this weekend?", "2026-11-21", "2026-11-22"),
    ("Jarvis, am I free Friday?", "2026-11-20", "2026-11-20"),
    ("Am I free next Friday?", "2026-11-27", "2026-11-27"),
])
def test_calendar_questions_go_to_the_skill(google, monkeypatch, said, first, last):
    from core import quick
    seen = []
    monkeypatch.setattr(gcalendar, "calendar_events", lambda f, t, q: seen.append((f, t)) or "ok")
    assert quick._calendar(said) == "ok" and seen == [(first, last)]   # 2026-11-20 is a Friday


def test_a_real_answer_from_quick(google):
    from core import quick
    _add(google, "Dentist", "2026-11-24T15:00:00", "2026-11-24T16:00:00")
    assert quick._calendar("Am I free on Tuesday?") == "Your calendar, Tuesday November 24: Dentist, 3 PM to 4 PM."


@pytest.mark.parametrize("said", ["Got it, sir. I've put a gym session on your calendar for tomorrow at 6 pm.",
                                  "I've blocked off Friday afternoon for the gym.", "I've rescheduled your dentist to Thursday."])
def test_calendar_done_claims_count_as_claims(said):
    from core import intelligence
    assert intelligence.claims_done(said)


def test_put_it_simply_is_not_a_claim():
    from core import intelligence
    assert not intelligence.claims_done("I've put it simply: the bigger number wins.")


def test_free_and_appointment_point_at_the_calendar_skills():
    from core import intelligence
    from tools import load_all, registry
    load_all()
    for said in ("Am I free on December 3rd?", "When is my dentist appointment?", "Do I have any appointments on the 3rd?"):
        names = [t["name"] for t in intelligence.select_tools([{"role": "user", "content": said}], registry.schemas())]
        assert "calendar_events" in names, (said, names)
