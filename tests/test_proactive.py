"""Offline tests for reminders, the briefing and price watches."""
from datetime import datetime, timedelta

import pytest

from tools import proactive


def test_one_off_reminder_fires_once():
    soon = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="minutes")
    assert "Reminder set" in proactive.set_reminder("stretch", soon, "none", False)
    assert proactive.due_reminders(datetime.now()) == []
    fired = proactive.due_reminders(datetime.now() + timedelta(minutes=6))
    assert [r["text"] for r in fired] == ["stretch"]
    assert "No reminders" in proactive.list_reminders()


def test_past_reminder_rejected():
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")
    assert "already passed" in proactive.set_reminder("x", past, "none", False)
    assert "ISO" in proactive.set_reminder("x", "tomorrow at 9", "none", False)


def test_weekday_reminder_skips_weekend():
    friday = datetime(2026, 10, 9, 7, 0)  # a Friday
    proactive.set_reminder("gym", friday.isoformat(timespec="minutes"), "weekdays", False)
    fired = proactive.due_reminders(friday + timedelta(minutes=1))
    assert len(fired) == 1
    nxt = datetime.fromisoformat(proactive.storage.load(proactive.REMINDERS, [])[0]["due"])
    assert nxt.weekday() == 0 and nxt.hour == 7  # Monday 07:00


def test_cancel_reminder():
    soon = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
    proactive.set_reminder("dentist", soon, "none", False)
    rid = proactive.list_reminders().split("]")[0].strip("[")
    assert proactive.cancel_reminder(rid) == "Reminder cancelled."


def test_briefing_info_collects_everything():
    from tools import notes

    notes.add_note("pay phone bill", "todo")
    proactive.set_reminder("call Marie", (datetime.now() + timedelta(minutes=30)).isoformat(timespec="minutes"), "none", False)
    info = proactive.briefing_info()
    if datetime.now().date() == (datetime.now() + timedelta(minutes=30)).date():
        assert "call Marie" in info
    assert "pay phone bill" in info and "Wardrobe: empty" in info


@pytest.mark.parametrize("html, price", [
    ('<script type="application/ld+json">{"offers": {"price": "39.90"}}</script>', 39.90),
    ('<meta property="product:price:amount" content="24.99">', 24.99),
    ('<span itemprop="price" content="59.00">$59</span>', 59.00),
    ("<div class='p'>Now only $19.99!</div>", 19.99),
])
def test_read_price(monkeypatch, html, price):
    from tools import studio

    monkeypatch.setattr(studio, "_get", lambda url: (html.encode(), "text/html"))
    assert proactive.read_price("https://shop.example/item") == pytest.approx(price)


def test_price_drop_alerts_once(monkeypatch):
    from tools import studio

    page = {"html": '"price": "40.00"'}
    monkeypatch.setattr(studio, "_get", lambda url: (page["html"].encode(), "text/html"))
    assert "Watching" in proactive.watch_price("https://shop.example/tee", "boxy tee", 30)
    assert proactive.check_prices() == []
    page["html"] = '"price": "28.00"'
    alerts = proactive.check_prices()
    assert len(alerts) == 1 and "28.00" in alerts[0]
    assert proactive.check_prices() == []  # same price: no repeat alert


def test_outfit_calendar(monkeypatch):
    import webbrowser
    from datetime import date

    from tools import fashion

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: None)
    today = date.today().isoformat()
    assert "Planned" in fashion.plan_outfit(today, "black tee, dark jeans", [], "work")
    assert "black tee" in fashion.outfit_calendar(3, True)
    assert "Outfit planned for today: black tee" in proactive.briefing_info()
    assert "ISO" in fashion.plan_outfit("next friday", "x", [], "y")


def test_collage_side_by_side():
    from PIL import Image

    from tools import studio

    board = studio._collage([("A", Image.new("RGB", (400, 600), "red")), ("B", Image.new("RGB", (300, 600), "blue"))])
    assert board.height == 954 and board.width > 1000


def test_yearly_reminder_rolls_to_next_year():
    from datetime import datetime

    from tools import proactive

    proactive.set_reminder("Maya's birthday is in a week", "2027-06-01T09:00", "yearly", False)
    fired = proactive.due_reminders(datetime(2027, 6, 1, 9, 1))
    assert [r["text"] for r in fired] == ["Maya's birthday is in a week"]
    nxt = [r for r in proactive.storage.load(proactive.REMINDERS, []) if r["text"].startswith("Maya")][0]
    assert nxt["due"] == "2028-06-01T09:00" and nxt["repeat"] == "yearly"
