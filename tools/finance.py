"""Household money: budgets (groceries, gas, eating out...) and spending, shared by the household so
both of you log to the same grocery budget. Jarvis warns at 80% and when a budget is blown.

Stored in data/household/finance.json. Amounts are in dollars.
"""
import re
from datetime import date, datetime, timedelta

from core import storage
from core.words import plural
from tools.registry import tool

FILE = "household/finance.json"
PERIODS = ["weekly", "monthly"]


def _data() -> dict:
    return storage.load(FILE, {"budgets": {}, "expenses": []})


def _cat(name: str) -> str:
    c = re.sub(r"\s+", " ", (name or "other").strip().lower())
    return {"grocery": "groceries", "food": "groceries", "fuel": "gas", "restaurants": "eating out",
            "restaurant": "eating out", "takeout": "eating out", "dining": "eating out"}.get(c, c)


def _period_start(period: str, today: date | None = None) -> date:
    today = today or date.today()
    if period == "weekly":
        return today - timedelta(days=today.weekday())  # Monday
    return today.replace(day=1)


def _period_end(period: str, start: date) -> date:
    if period == "weekly":
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _spent(data: dict, category: str, since: date) -> float:
    return round(sum(e["amount"] for e in data["expenses"]
                     if e["category"] == category and date.fromisoformat(e["t"][:10]) >= since), 2)


def _status_line(data: dict, category: str) -> str:
    b = data["budgets"][category]
    start = _period_start(b["period"])
    spent = _spent(data, category, start)
    left = round(b["amount"] - spent, 2)
    pct = spent / b["amount"] * 100 if b["amount"] else 0
    days_left = (_period_end(b["period"], start) - date.today()).days
    tag = " OVER BUDGET" if left < 0 else (" (80%+ used)" if pct >= 80 else "")
    return (f"{category}: ${spent:,.2f} of ${b['amount']:,.2f} this {b['period'][:-2]}, "
            f"${left:,.2f} left, {plural(days_left, 'day')} to go{tag}")


@tool(
    "Set (or change) a household budget for a spending category, e.g. groceries $600 a month or gas $50 a week.",
    {"category": {"type": "string", "description": "e.g. groceries, gas, eating out, entertainment"},
     "amount": {"type": "number"}, "period": {"type": "string", "enum": PERIODS}},
)
def set_budget(category: str, amount: float, period: str) -> str:
    data = _data()
    cat = _cat(category)
    data["budgets"][cat] = {"amount": round(float(amount), 2), "period": period}
    storage.save(FILE, data)
    return f"Budget set. {_status_line(data, cat)}"


@tool(
    "Log money spent (a grocery run, gas, a dinner out). Tells how much of that budget is left and warns when "
    "it's nearly or completely used. Use whenever they mention spending: 'I spent 84 at Walmart on groceries'.",
    {"amount": {"type": "number"}, "category": {"type": "string"},
     "note": {"type": "string", "description": "Where / what, e.g. 'Walmart', 'commissary weekly shop'"}},
    required=["amount", "category"],
)
def log_expense(amount: float, category: str, note: str = "") -> str:
    from core import users

    data = _data()
    cat = _cat(category)
    data["expenses"].append({"t": datetime.now().isoformat(timespec="minutes"), "amount": round(float(amount), 2),
                             "category": cat, "note": (note or "").strip(), "who": users.current()})
    data["expenses"] = data["expenses"][-5000:]
    storage.save(FILE, data)
    where = f", {note}" if note else ""
    if cat in data["budgets"]:
        return f"Logged ${float(amount):,.2f} ({cat}{where}). {_status_line(data, cat)}."
    return f"Logged ${float(amount):,.2f} ({cat}{where}). There's no {cat} budget yet; offer to set one."


@tool("Where every budget stands right now (spent, left, days to go), or just one category.",
      {"category": {"type": "string", "description": "Optional: one category, e.g. groceries"}}, required=[])
def budget_status(category: str = "") -> str:
    data = _data()
    if not data["budgets"]:
        return "No budgets yet. Suggest starting with groceries."
    cats = [_cat(category)] if category else sorted(data["budgets"])
    lines = [_status_line(data, c) for c in cats if c in data["budgets"]]
    return "\n".join(lines) or f"No budget called {category}."


@tool(
    "Spending summary for a period: totals per category, biggest purchases, compared with the budgets. "
    "Good for 'how did we do on groceries this month?'.",
    {"period": {"type": "string", "enum": ["this week", "this month", "last month", "last 30 days"]}},
)
def spending_summary(period: str) -> str:
    data = _data()
    today = date.today()
    if period == "this week":
        start, end = _period_start("weekly"), today
    elif period == "last month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif period == "last 30 days":
        start, end = today - timedelta(days=30), today
    else:
        start, end = today.replace(day=1), today
    rows = [e for e in data["expenses"] if start <= date.fromisoformat(e["t"][:10]) <= end]
    if not rows:
        return f"Nothing logged for {period}."
    totals: dict[str, float] = {}
    for e in rows:
        totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]
    lines = [f"{period.capitalize()} ({start:%b %d}-{end:%b %d}): ${sum(totals.values()):,.2f} total"]
    for c, v in sorted(totals.items(), key=lambda x: -x[1]):
        b = data["budgets"].get(c)
        lines.append(f"- {c}: ${v:,.2f}" + (f" (budget ${b['amount']:,.0f}/{b['period'][:-2]})" if b else ""))
    top = sorted(rows, key=lambda e: -e["amount"])[:3]
    lines.append("Biggest: " + "; ".join(f"${e['amount']:,.2f} {e['category']}" + (f" ({e['note']})" if e["note"] else "")
                                       for e in top))
    return "\n".join(lines)


@tool("Remove the last expense that was logged (a mistake or a duplicate).")
def undo_last_expense() -> str:
    data = _data()
    if not data["expenses"]:
        return "Nothing to undo."
    e = data["expenses"].pop()
    storage.save(FILE, data)
    return f"Removed ${e['amount']:,.2f} ({e['category']}{', ' + e['note'] if e['note'] else ''})."
