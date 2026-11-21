"""The tap-through Google Calendar sign-in (app/calendar.html): save key -> Connect -> Allow -> paste back or loopback.
Google is faked; nothing leaves the machine except the loopback request to our own listener."""
import urllib.parse
import urllib.request

import httpx
import pytest

from core import gcal, storage, users
from tests.test_app import client, pair  # noqa: F401 - the shared app-server fixture

CID = "123-abc.apps.googleusercontent.com"


@pytest.fixture
def google(monkeypatch):
    seen = []

    def post(url, data=None, timeout=None):
        seen.append(data)
        if data.get("code") == "bad":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt-" + str(data.get("code")), "expires_in": 3600})
    monkeypatch.setattr(gcal.httpx, "post", post)
    gcal._pending.clear()
    yield seen
    gcal._pending.clear()


def _q(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def test_key_is_checked_and_json_accepted():
    with pytest.raises(gcal.CalendarError, match="googleusercontent"):
        gcal.save_client(client_id="not-a-key", client_secret="GOCSPX-abcdefghij")
    with pytest.raises(gcal.CalendarError, match="cut off"):
        gcal.save_client(client_id=CID, client_secret="short")
    gcal.save_client(raw='{"installed": {"client_id": "%s", "client_secret": "GOCSPX-abcdefghij"}}' % CID)
    assert gcal.saved_client() == {"client_id": CID, "client_secret": "GOCSPX-abcdefghij"}


def test_web_key_is_turned_away():
    with pytest.raises(gcal.CalendarError, match="Desktop app"):
        gcal.save_client(raw='{"web": {"client_id": "%s", "client_secret": "GOCSPX-abcdefghij"}}' % CID)


def test_removed_person_never_gets_a_token(google):
    gcal.save_client(client_id=CID, client_secret="GOCSPX-abcdefghij")
    q = _q(gcal.start_link("ghost", listen=False))
    with pytest.raises(gcal.CalendarError, match="household"):
        gcal.finish_link("ghost", f"http://127.0.0.1:1/?state={q['state']}&code=c9")
    assert not google


def test_start_needs_the_key_first():
    with pytest.raises(gcal.CalendarError, match="key first"):
        gcal.start_link("owner", listen=False)


def test_pasted_address_finishes_for_the_person_who_started(google):
    gcal.save_client(client_id=CID, client_secret="GOCSPX-abcdefghij")
    url = gcal.start_link("owner", listen=False)
    q = _q(url)
    assert q["client_id"] == CID and q["code_challenge_method"] == "S256" and q["access_type"] == "offline"
    back = q["redirect_uri"] + "?" + urllib.parse.urlencode({"state": q["state"], "code": "c1", "scope": gcal.SCOPE})
    with pytest.raises(gcal.CalendarError, match="older try"):
        gcal.finish_link("someone-else", back)                    # another person can't finish his sign-in
    assert gcal.finish_link("owner", back) == {"connected": True}
    with users.acting_as("owner"):
        rec = storage.load(gcal.TOKEN_FILE, {})
    assert rec["refresh_token"] == "rt-c1" and rec["client_id"] == CID
    assert google[-1]["redirect_uri"] == q["redirect_uri"] and google[-1]["code_verifier"]
    assert gcal.finish_link("owner", back) == {"connected": True}  # pasting twice is harmless
    assert len(google) == 1


def test_cancel_and_bad_code_are_said_plainly(google):
    gcal.save_client(client_id=CID, client_secret="GOCSPX-abcdefghij")
    q = _q(gcal.start_link("owner", listen=False))
    with pytest.raises(gcal.CalendarError, match="Cancel"):
        gcal.finish_link("owner", f"http://127.0.0.1:1/?state={q['state']}&error=access_denied")
    assert "Cancel" in gcal.link_status("owner")["error"]
    q = _q(gcal.start_link("owner", listen=False))                  # a new tap replaces the failed try
    assert gcal.link_status("owner")["error"] == ""
    with pytest.raises(gcal.CalendarError, match="refused"):
        gcal.finish_link("owner", f"http://127.0.0.1:1/?state={q['state']}&code=bad")
    with pytest.raises(gcal.CalendarError, match="whole address"):
        gcal.finish_link("owner", "hello")
    with users.acting_as("owner"):
        assert not gcal.connected()


def test_pc_loopback_finishes_by_itself(google):
    gcal.save_client(client_id=CID, client_secret="GOCSPX-abcdefghij")
    q = _q(gcal.start_link("owner"))
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and not q["redirect_uri"].endswith(":1/")
    with pytest.raises(Exception):                                   # a stray request without our state is refused
        urllib.request.urlopen(q["redirect_uri"] + "?code=x", timeout=5)
    page = urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode({"state": q["state"], "code": "c2"}),
                                  timeout=5).read().decode()
    assert "connected" in page
    assert gcal.link_status("owner")["connected"]


def test_api_walk_through(client, google):  # noqa: F811 - pytest fixture
    h = pair(client)
    assert client.get("/api/calendar/link", headers=h).json() == {
        "connected": False, "has_client": False, "error": "", "pending": False, "can_set_key": True}
    wife = users.add("Wife", {"en": "ma'am"})
    hw = pair(client, wife)
    r = client.post("/api/calendar/link/key", headers=hw, json={"client_id": CID, "client_secret": "GOCSPX-abcdefghij"})
    assert r.status_code == 403                                       # only the owner sets the family's key
    assert client.get("/api/calendar/link", headers=hw).json()["can_set_key"] is False
    r = client.post("/api/calendar/link/key", headers=h, json={"client_id": "nope", "client_secret": "x"})
    assert r.status_code == 400 and "googleusercontent" in r.json()["detail"]
    r = client.post("/api/calendar/link/key", headers=h, json={"client_id": CID, "client_secret": "GOCSPX-abcdefghij"})
    assert r.json()["has_client"]
    url = client.post("/api/calendar/link/start", headers=h).json()["url"]
    assert client.get("/api/calendar/link", headers=h).json()["pending"]   # a reload still shows the paste step
    q = _q(url)
    back = q["redirect_uri"] + "?" + urllib.parse.urlencode({"state": q["state"], "code": "c3"})
    assert client.post("/api/calendar/link/finish", headers=h, json={"url": back}).json()["connected"]
    assert client.get("/api/calendar/link").status_code == 401        # every route needs a linked phone
    assert not client.post("/api/calendar/link/disconnect", headers=h).json()["connected"]


def test_page_and_tile():
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    page = (app / "calendar.html").read_text(encoding="utf-8")
    for route in ("/api/calendar/link", "/api/calendar/link/key", "/api/calendar/link/start",
                  "/api/calendar/link/finish", "/api/calendar/today"):
        assert route in page
    assert "console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com" in page
    assert "/calendar.html" in (app / "index.html").read_text(encoding="utf-8")
