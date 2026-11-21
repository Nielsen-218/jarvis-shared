"""Things Jarvis does on his own: reminders, the morning briefing, and price watches.
The checks run in core/scheduler.py; these are the tools to set them up."""
import json
import re
import uuid
from datetime import date, datetime, timedelta

from core import storage
from core.words import plural
from tools.registry import tool

REMINDERS = "reminders.json"
WATCHES = "price_watch.json"
REPEATS = ["none", "daily", "weekdays", "weekly", "yearly"]


# -- reminders ------------------------------------------------------------------------
HOUSEHOLD_REMINDERS = "household/reminders.json"


@tool(
    "Set a reminder that Jarvis will say out loud (and send as a notification, also to their phone "
    "if paired) at a given time. Work out the exact local date and time from the background block's "
    "current time. Household reminders go to everyone (bins night, family dinner).",
    {
        "text": {"type": "string", "description": "What to remind them, e.g. 'call Marie back'"},
        "when": {"type": "string", "description": "Local date and time, ISO format: '2026-10-11T09:00'"},
        "repeat": {"type": "string", "enum": REPEATS},
        "shared": {"type": "boolean", "description": "true = household reminder for everyone; false = just this person"},
    },
)
def set_reminder(text: str, when: str, repeat: str, shared: bool) -> str:
    try:
        due = datetime.fromisoformat(when)
    except ValueError:
        return "Couldn't read that time; use ISO format like 2026-10-11T09:00."
    if due < datetime.now() - timedelta(minutes=1) and repeat == "none":
        return "That time has already passed."
    file = HOUSEHOLD_REMINDERS if shared else REMINDERS
    items = storage.load(file, [])
    rid = ("h" if shared else "") + uuid.uuid4().hex[:5]
    items.append({"id": rid, "text": text, "due": due.isoformat(timespec="minutes"), "repeat": repeat})
    storage.save(file, items)
    if repeat == "none":       # a standing repeat is a rule, not a promise; a one-off is owed at its time (core/commitments)
        try:
            from core import commitments
            commitments.on_reminder(rid, text, due, shared)
        except Exception as error:  # noqa: BLE001 - the reminder is set either way
            print(f"[commitments] reminder: {type(error).__name__}: {error}")
    return (f"{'Household reminder' if shared else 'Reminder'} set for {due.strftime('%A %d %B at %H:%M')}"
            + (f", repeating {repeat}." if repeat != "none" else "."))


def _my_reminders() -> list[dict]:
    return storage.load(REMINDERS, []) + storage.load(HOUSEHOLD_REMINDERS, [])


@tool("List upcoming reminders (this person's and the household's).")
def list_reminders() -> str:
    items = sorted(_my_reminders(), key=lambda r: r["due"])
    if not items:
        return "No reminders set."
    return "\n".join(f"[{r['id']}] {r['due'].replace('T', ' ')} - {r['text']}"
                     + (" (household)" if r["id"].startswith("h") else "")
                     + (f" (repeats {r['repeat']})" if r["repeat"] != "none" else "") for r in items)


@tool("Cancel a reminder (use list_reminders for the id).", {"reminder_id": {"type": "string"}})
def cancel_reminder(reminder_id: str) -> str:
    file = HOUSEHOLD_REMINDERS if reminder_id.startswith("h") else REMINDERS
    items = storage.load(file, [])
    keep = [r for r in items if r["id"] != reminder_id]
    if len(keep) == len(items):
        return f"No reminder with id {reminder_id}."
    storage.save(file, keep)
    return "Reminder cancelled."


def due_reminders(now: datetime | None = None, file: str = REMINDERS) -> list[dict]:
    """Pop reminders that are due (rescheduling repeating ones). Called by the scheduler,
    once per person (file=REMINDERS) and once for the household (file=HOUSEHOLD_REMINDERS)."""
    now = now or datetime.now()
    items = storage.load(file, [])
    fired, keep = [], []
    for r in items:
        due = datetime.fromisoformat(r["due"])
        if due > now:
            keep.append(r)
            continue
        fired.append(r)
        if r["repeat"] == "yearly":  # birthdays, anniversaries: same date next year (Feb 29 -> Feb 28)
            nxt = due
            while nxt <= now:
                try:
                    nxt = nxt.replace(year=nxt.year + 1)
                except ValueError:
                    nxt = nxt.replace(year=nxt.year + 1, day=28)
            keep.append({**r, "due": nxt.isoformat(timespec="minutes")})
        elif r["repeat"] != "none":
            step = {"daily": 1, "weekly": 7, "weekdays": 1}[r["repeat"]]
            nxt = due + timedelta(days=step)
            while nxt <= now or (r["repeat"] == "weekdays" and nxt.weekday() >= 5):
                nxt += timedelta(days=1 if r["repeat"] != "weekly" else 7)
            keep.append({**r, "due": nxt.isoformat(timespec="minutes")})
    if fired:
        storage.save(file, keep)
    return fired


# -- morning briefing ------------------------------------------------------------------
@tool(
    "Gather everything for a daily briefing: today's calendar events, reminders, open to-dos, review words due, "
    "recent outfits and the style profile. Then add the weather (get_weather) and suggest an "
    "outfit from their wardrobe. Use when they ask for their briefing, or for the automatic morning one.",
)
def briefing_info() -> str:
    from tools import fashion

    today = date.today().isoformat()
    lines = [f"Date: {datetime.now().strftime('%A %d %B %Y, %H:%M')}"]
    todays = [r for r in _my_reminders() if r["due"].startswith(today)]
    from tools import gcalendar

    cal = gcalendar.today_summary()
    if cal:
        lines.append(cal)
    lines.append("Reminders today: " + ("; ".join(f"{r['due'][11:]} {r['text']}" for r in todays) or "none"))
    from tools.notes import _all_notes

    notes = _all_notes()
    todos = [n["text"] for n in notes if n["list"] == "todo" and not n["done"]]
    lines.append("Open to-dos: " + ("; ".join(todos) or "none"))
    shopping = [n["text"] for n in notes if n["list"] == "shopping" and not n["done"]]
    if shopping:
        lines.append("Household shopping list: " + "; ".join(shopping))
    tutor = storage.load("tutor.json", {})
    due = {lang: sum(w["due"] <= today for w in info["words"]) for lang, info in tutor.items()}
    lines.append("Language review due: " + (", ".join(plural(n, f"{l} word") for l, n in due.items() if n) or "none"))
    plan = fashion._load("calendar.json", {}).get(today)
    lines.append("Outfit planned for today: " + (f"{plan['description']} ({plan['occasion']})" if plan else "none"))
    lines.append("Recent outfits: " + fashion.recent_outfits(3).replace("\n", "; "))
    lines.append("Style profile: " + fashion.get_style_profile().replace("\n", "; "))
    wardrobe = fashion.list_wardrobe("all")
    lines.append("Wardrobe: " + (wardrobe.replace("\n", "; ") if "No items" not in wardrobe else "empty"))
    watches = storage.load(WATCHES, [])
    drops = [w for w in watches if w.get("dropped")]
    if drops:
        lines.append("Price drops: " + "; ".join(f"{w['name']} now {w['last_price']}" for w in drops))
    return "\n".join(lines)


# -- price watch -------------------------------------------------------------------------
@tool(
    "Watch a product page and tell the user when the price drops (checked every few hours). "
    "Use for things they want to buy, e.g. from a lookbook.",
    {
        "url": {"type": "string"},
        "name": {"type": "string", "description": "Short product name"},
        "target_price": {"type": "number", "description": "Alert at or below this price; 0 = any drop"},
    },
)
def watch_price(url: str, name: str, target_price: float) -> str:
    price = None
    try:
        price = read_price(url)
    except Exception as e:
        return f"I couldn't read a price from that page ({e}). Stores sometimes block automated checks; try another link."
    items = storage.load(WATCHES, [])
    items.append({"id": uuid.uuid4().hex[:5], "url": url, "name": name, "target": target_price,
                  "first_price": price, "last_price": price, "checked": datetime.now().isoformat(timespec="minutes"),
                  "dropped": False})
    storage.save(WATCHES, items)
    return f"Watching {name}: currently {price:.2f}." + (f" I'll tell you at or below {target_price:.2f}." if target_price else " I'll tell you if it drops.")


@tool("List price watches.")
def list_price_watches() -> str:
    items = storage.load(WATCHES, [])
    if not items:
        return "No price watches."
    return "\n".join(f"[{w['id']}] {w['name']}: {w['last_price']} (was {w['first_price']}"
                     + (f", target {w['target']}" if w["target"] else "") + f") {w['url']}" for w in items)


@tool("Stop watching a price.", {"watch_id": {"type": "string"}})
def stop_price_watch(watch_id: str) -> str:
    items = storage.load(WATCHES, [])
    keep = [w for w in items if w["id"] != watch_id]
    storage.save(WATCHES, keep)
    return "Stopped." if len(keep) < len(items) else f"No watch with id {watch_id}."


_PRICE_PATTERNS = [
    r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]{2})?)"?',
    r'property=["\'](?:product|og):price:amount["\']\s+content=["\']([0-9.,]+)',
    r'content=["\']([0-9.,]+)["\']\s+property=["\'](?:product|og):price:amount',
    r'itemprop=["\']price["\'][^>]*content=["\']([0-9.,]+)',
    r'\$\s?([0-9]{1,5}(?:\.[0-9]{2}))',
]


def read_price(url: str) -> float:
    from tools.studio import _get

    data, _ = _get(url)
    page = data.decode("utf-8", errors="ignore")
    for pattern in _PRICE_PATTERNS:
        m = re.search(pattern, page)
        if m:
            value = float(m.group(1).replace(",", "."))
            if 0 < value < 100000:
                return value
    raise ValueError("no price found on the page")


def check_prices() -> list[str]:
    """Re-check every watch; returns alert messages for drops. Called by the scheduler."""
    items = storage.load(WATCHES, [])
    alerts = []
    for w in items:
        try:
            price = read_price(w["url"])
        except Exception:
            continue
        hit_target = w["target"] and price <= w["target"]
        dropped = price < (w["last_price"] or price)
        if (hit_target or (not w["target"] and dropped)) and not w.get("alerted_at") == price:
            alerts.append(f"{w['name']} dropped to {price:.2f} (was {w['first_price']:.2f}).")
            w["alerted_at"] = price
            w["dropped"] = True
        w["last_price"] = price
        w["checked"] = datetime.now().isoformat(timespec="minutes")
    storage.save(WATCHES, items)
    return alerts
