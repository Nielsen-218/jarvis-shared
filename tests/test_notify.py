"""Notifications: categories, quiet hours, history, PC->laptop forwarding, nudges, and the phone API."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from core import hud, notify, users


@pytest.fixture(autouse=True)
def quiet_hud(monkeypatch):
    monkeypatch.setattr(hud, "_send", lambda m: None)


# -- preferences ------------------------------------------------------------------------------------
def test_defaults_everything_on_with_quiet_hours_overnight():
    p = notify.prefs("owner")
    assert all(p["categories"].values()) and p["quiet"] == {"on": True, "start": "22:00", "end": "07:00"}


@pytest.mark.parametrize("hm, quiet", [("23:30", True), ("02:00", True), ("06:59", True), ("07:00", False), ("13:00", False), ("21:59", False)])
def test_quiet_hours_wrap_past_midnight(hm, quiet):
    h, m = map(int, hm.split(":"))
    assert notify.in_quiet_hours(notify.prefs("owner"), datetime(2026, 10, 22, h, m)) == quiet


def test_categories_and_quiet_hours_decide_what_reaches_the_phone():
    night = datetime(2026, 10, 22, 23, 30)
    assert notify.allowed("owner", "reminders", True, night)          # urgent: always
    assert not notify.allowed("owner", "alerts", False, night)        # held overnight
    assert notify.allowed("owner", "alerts", False, datetime(2026, 10, 22, 12, 0))
    notify.set_prefs("owner", {"training": False}, {"start": "23:00", "end": "06:30", "on": True})
    assert not notify.allowed("owner", "training", False, datetime(2026, 10, 22, 12, 0))
    p = notify.prefs("owner")
    assert p["quiet"]["start"] == "23:00" and p["categories"]["training"] is False
    notify.set_prefs("owner", None, {"start": "25:99"})                # nonsense times are ignored
    assert notify.prefs("owner")["quiet"]["start"] == "23:00"


# -- push + history ---------------------------------------------------------------------------------
def test_push_respects_prefs_and_keeps_history(monkeypatch):
    import pywebpush

    from core import appserver
    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **k: sent.append(k))
    devices = users.get("owner").get("devices", []) + [{"id": "p1", "token_hash": "x", "name": "phone", "push": {"endpoint": "https://e"}}]
    users.update("owner", devices=devices)
    assert appserver.push("Timer done", "Your tea timer is done.", "owner", "reminders", urgent=True) == 1
    notify.set_prefs("owner", {"alerts": False})
    assert appserver.push("Homelab problem", "metube is down.", "owner", "alerts") == 0
    assert len(sent) == 1
    h = notify.history("owner")
    assert [(r["title"], r["pushed"]) for r in h[:2]] == [("Homelab problem", False), ("Timer done", True)]


def test_the_pc_forwards_to_the_laptop_and_keeps_it_locally_if_unreachable(monkeypatch):
    from tools import vault
    calls = []
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "t"))
    monkeypatch.setattr(vault, "_call", lambda m, p, b=None, q=None: calls.append((p, b)))
    notify.forward_to_server("Timer done", "Your tea timer is done.", "owner", "reminders", True)
    assert calls == [("/api/notify", {"title": "Timer done", "body": "Your tea timer is done.", "uid": "owner",
                                      "category": "reminders", "urgent": True})]

    def down(*a, **k):
        raise OSError("unreachable")
    monkeypatch.setattr(vault, "_call", down)
    notify.forward_to_server("New badge", "First sweat", "owner", "training")
    assert notify.history("owner")[0]["title"] == "New badge"


def test_notify_passes_category_to_channels_that_want_it():
    got = []

    def channel(title, msg, uid, category="jarvis", urgent=False):
        got.append((title, uid, category, urgent))
    channel.wants_category = True
    notify._channels.append(channel)
    try:
        notify.notify("Price drop", "Eggs are $3", "owner", "alerts")
        notify.notify("Odd", "x", None, "nonsense")
    finally:
        notify._channels.remove(channel)
    assert got == [("Price drop", "owner", "alerts", False), ("Odd", None, "jarvis", False)]


# -- sources ----------------------------------------------------------------------------------------
def test_timer_done_speaks_and_notifies_urgently(monkeypatch):
    from tools import system
    said, sent = [], []
    monkeypatch.setattr(system, "announce", said.append)
    monkeypatch.setattr(notify, "notify", lambda *a, **k: sent.append((a, k)))
    system.timer_done("tea", "owner")
    assert said == ["Your tea timer is done."]
    assert sent == [(("Timer done", "Your tea timer is done.", "owner", "reminders"), {"urgent": True})]


def test_training_nudges_once_a_day_and_the_sunday_report(monkeypatch):
    from core import scheduler
    from tools import fitness
    sent = []
    monkeypatch.setattr(notify, "notify", lambda t, b, uid=None, category="jarvis", urgent=None: sent.append((t, uid, category)))
    monkeypatch.setattr(fitness, "app_data", lambda: {"today": {"title": "Upper B", "items": [{"exercise": "x"}]},
                                                      "workouts": [], "checkin": None})
    s = scheduler.Scheduler(say=lambda t: None, ask=lambda t: None)
    s._nudges(datetime(2026, 10, 22, 9, 0))
    s._nudges(datetime(2026, 10, 22, 9, 30))                            # already sent today
    assert [x for x in sent if x[1] == "owner"] == [("Morning check-in", "owner", "training")]
    s._nudges(datetime(2026, 10, 22, 18, 30))
    assert ("Today's workout is waiting", "owner", "training") in sent
    s._nudges(datetime(2026, 10, 25, 19, 0))                            # Sunday evening
    assert ("Your week with Jarvis", "owner", "weekly") in sent


# -- API --------------------------------------------------------------------------------------------
from tests.test_app import client, pair  # noqa: E402,F401


def test_api_settings_history_and_relay(client, monkeypatch):
    h = pair(client)
    r = client.get("/api/notifications", headers=h).json()
    assert {c["id"] for c in r["categories"]} == set(notify.CATEGORIES) and r["push"] is False
    assert client.post("/api/notifications/prefs", json={"categories": {"weekly": False}, "quiet": {"start": "21:30"}},
                       headers=h).json()["prefs"]["quiet"]["start"] == "21:30"
    assert client.post("/api/notifications/test", headers=h).status_code == 400    # this device hasn't turned them on
    relayed = []
    monkeypatch.setattr(notify, "notify", lambda *a, **k: relayed.append(a))
    assert client.post("/api/notify", json={"title": "Timer done", "body": "Tea.", "uid": "owner", "category": "reminders",
                                            "urgent": True}, headers=h).json() == {"ok": True}
    assert relayed == [("Timer done", "Tea.", "owner", "reminders", True)]
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert client.post("/api/notify", json={"title": "x", "body": "y"}, headers=pair(client, wife)).status_code == 403


# -- reminders have one owner -----------------------------------------------------------------------
def _due_in(minutes_ago):
    from datetime import timedelta
    from core import storage
    from tools import proactive
    when = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="minutes")
    storage.save(proactive.REMINDERS, [{"id": "r1", "text": "call the dentist", "due": when, "repeat": "none"}])


def test_server_fires_reminders_notifies_and_has_the_pc_say_them(monkeypatch):
    from core import pcbridge, scheduler
    sent, said, relayed = [], [], []
    monkeypatch.setattr(scheduler, "ROLE", "server")
    monkeypatch.setattr(notify, "notify", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(pcbridge, "json_request", lambda path, method="GET", body=None: relayed.append((path, body)) or {"spoken": True})
    _due_in(0)
    scheduler.Scheduler(say=said.append, ask=lambda t: None)._reminders()
    assert sent == [("Reminder", "call the dentist", "owner", "reminders")]
    assert relayed == [("/api/pc/announce", {"text": "A reminder, sir: call the dentist.", "for": "owner"})] and said == []


def test_pc_leaves_fresh_reminders_to_the_server_but_backs_it_up(monkeypatch):
    from core import scheduler
    said = []
    monkeypatch.setattr(scheduler, "ROLE", "desk")
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)
    _due_in(0)
    s = scheduler.Scheduler(say=said.append, ask=lambda t: None)
    s._reminders()
    assert said == []                       # the server has it
    _due_in(3)                              # three minutes late: the server must be down
    s._reminders()
    assert said == ["A reminder, sir: call the dentist."]


# ---- cross-device audit B (2026-11-13): quiet hours follow the owner's timezone, not the host's clock ----------------------
def _at_utc(monkeypatch, y, mo, d, h, mi):
    """Pin the clock: ownertime.now() reads datetime.now(tz) - give it a fixed instant."""
    from datetime import datetime as real, timezone

    from core import ownertime
    instant = real(y, mo, d, h, mi, tzinfo=timezone.utc)

    class Fixed(real):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(ownertime, "datetime", Fixed)


@pytest.mark.parametrize("utc, quiet", [((2026, 11, 13, 3, 30), True),    # 22:30 EST the evening before: quiet
                                        ((2026, 11, 13, 23, 0), False),   # 18:00 EST: NOT quiet (a UTC host would say 23:00 = quiet)
                                        ((2026, 11, 13, 12, 30), False),  # 07:30 EST
                                        ((2026, 7, 1, 2, 30), True),      # 22:30 EDT
                                        ((2026, 7, 1, 10, 30), True)])    # 06:30 EDT (06:30 UTC would be 02:30 EDT: also quiet, 10:30 UTC is a UTC host at 10:30: not)
def test_quiet_hours_use_the_owner_clock(monkeypatch, utc, quiet):
    from core import ownertime
    monkeypatch.delenv("JARVIS_TZ", raising=False)
    monkeypatch.setattr(ownertime, "_zone", lambda name: None)      # no tz database: the built-in Eastern rule
    _at_utc(monkeypatch, *utc)
    assert notify.in_quiet_hours(notify.prefs("owner")) == quiet


def test_the_built_in_eastern_rule_switches_on_the_right_days():
    from datetime import datetime as dt

    from core import ownertime
    assert ownertime._us_eastern(dt(2026, 3, 8, 6, 59)) == dt(2026, 3, 8, 1, 59)     # still EST
    assert ownertime._us_eastern(dt(2026, 3, 8, 7, 0)) == dt(2026, 3, 8, 3, 0)       # clocks jump to EDT
    assert ownertime._us_eastern(dt(2026, 11, 1, 5, 59)) == dt(2026, 11, 1, 1, 59)   # EDT
    assert ownertime._us_eastern(dt(2026, 11, 1, 6, 0)) == dt(2026, 11, 1, 1, 0)     # back to EST


def test_jarvis_tz_overrides_the_default(monkeypatch):
    from core import ownertime
    monkeypatch.setenv("JARVIS_TZ", "Not/AZone")
    assert ownertime.tz_name() == "Not/AZone"
    assert isinstance(ownertime.now(), datetime)                      # unknown zone: the host's own clock, never a crash


# ---- cross-device audit D (2026-11-13): a PC notification raised while the laptop is away waits in a small outbox -----------
@pytest.fixture
def laptop(monkeypatch):
    """The PC with a laptop link whose state the test flips; every POST /api/notify it receives is kept."""
    from tools import vault
    state = {"up": False, "got": []}
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "t"))

    def call(method, path, body=None, query=None):
        if not state["up"]:
            raise OSError("unreachable")
        state["got"].append(body)
        return {}
    monkeypatch.setattr(vault, "_call", call)
    monkeypatch.setattr(notify.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))   # no real threads
    return state


def _clock(monkeypatch, t):
    monkeypatch.setattr(notify.time, "time", lambda: t[0])


def test_a_notification_raised_while_the_laptop_is_away_is_sent_on_the_next_contact_once(laptop, monkeypatch):
    t = [1_000_000.0]
    _clock(monkeypatch, t)
    notify.forward_to_server("Timer done", "Your tea timer is done.", "owner", "reminders", True)
    assert laptop["got"] == [] and len(notify.outbox()) == 1
    laptop["up"] = True
    t[0] += 600
    notify.forward_to_server("Reminder", "Call the dentist.", "owner", "reminders", True)      # the next successful contact
    assert [b["title"] for b in laptop["got"]] == ["Reminder", "Timer done"]
    assert laptop["got"][1]["urgent"] is True and laptop["got"][1]["uid"] == "owner"
    assert notify.outbox() == []
    assert notify.history("owner") == [], "the laptop holds it now: the PC's own 'not pushed' copy is dropped"
    notify.flush_outbox()
    notify.flush_outbox()
    assert len(laptop["got"]) == 2, "never delivered twice"


def test_the_outbox_drops_what_is_older_than_an_hour_and_keeps_a_small_cap(laptop, monkeypatch):
    t = [1_000_000.0]
    _clock(monkeypatch, t)
    notify.forward_to_server("Too old", "x", "owner", "alerts")
    t[0] += notify.OUTBOX_TTL + 1
    for i in range(notify.OUTBOX_MAX + 5):
        notify.forward_to_server(f"n{i}", "x", "owner", "alerts")
    queued = [r["body"]["title"] for r in notify.outbox()]
    assert len(queued) == notify.OUTBOX_MAX and queued[-1] == f"n{notify.OUTBOX_MAX + 4}" and "Too old" not in queued and "n0" not in queued
    laptop["up"] = True
    t[0] += notify.OUTBOX_TTL - 5
    notify.flush_outbox()
    assert len(laptop["got"]) == notify.OUTBOX_MAX
    t[0] += 10
    laptop["got"].clear()
    notify.forward_to_server("late", "x", "owner", "alerts")
    assert [b["title"] for b in laptop["got"]] == ["late"]


def test_a_failed_flush_keeps_the_rest_for_next_time(laptop, monkeypatch):
    t = [1_000_000.0]
    _clock(monkeypatch, t)
    notify.forward_to_server("one", "x", "owner", "alerts")
    notify.forward_to_server("two", "x", "owner", "alerts")
    notify.flush_outbox()                                              # still down
    assert [r["body"]["title"] for r in notify.outbox()] == ["one", "two"]


def test_the_default_outbox_is_machine_state_never_the_synced_data_folder(monkeypatch):
    from core.config import ROOT
    monkeypatch.delenv("JARVIS_OUTBOX_DIR", raising=False)
    path = notify.outbox_path()
    assert (ROOT / "machine-state") in path.parents and (ROOT / "data") not in path.parents


def test_a_laptop_that_refuses_the_notification_for_good_is_not_retried(monkeypatch):
    import urllib.error

    from tools import vault
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "t"))

    def refuse(*a, **k):
        raise urllib.error.HTTPError("https://laptop/api/notify", 400, "bad", None, None)
    monkeypatch.setattr(vault, "_call", refuse)
    notify.forward_to_server("x", "y", "owner", "alerts")
    assert notify.outbox() == []


def test_a_single_machine_with_no_laptop_queues_nothing(monkeypatch):
    from tools import vault
    monkeypatch.setattr(vault, "_remote", lambda: None)
    notify.forward_to_server("x", "y", "owner", "alerts")
    assert notify.outbox() == [] and notify.history("owner")[0]["title"] == "x"


def test_a_successful_vault_call_flushes_the_outbox_in_the_background(monkeypatch):
    import io

    from tools import vault
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "t"))
    seen = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(vault.urllib.request, "urlopen", lambda req, timeout=0: seen.append(req.full_url) or Reply(b"{}"))
    monkeypatch.setattr(notify.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    notify.enqueue({"title": "waiting", "body": "x", "uid": "owner", "category": "alerts", "urgent": False})
    vault._call("GET", "/api/vault/search")                                  # any successful contact with the laptop
    assert seen == ["https://laptop/api/vault/search", "https://laptop/api/notify"] and notify.outbox() == []
