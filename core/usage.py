"""Tracks what Jarvis costs (Claude API) and enforces a monthly budget.

Prices are per million tokens (USD), from Anthropic's published rates.
"""
import json
import os
import threading
from datetime import date
from pathlib import Path

from core import storage

PRICES = {  # input, output, cache read, cache write
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
WEB_SEARCH_PRICE = 10.00 / 1000  # per search
FILE = "usage.json"
_lock = threading.RLock()


def _load() -> dict:
    """The ledger. JARVIS_USAGE_FILE points a separate process (the practice watcher in a worktree) at live Jarvis's ledger,
    so a teacher's spending is counted against the same monthly budget and credit as everything else."""
    path = os.getenv("JARVIS_USAGE_FILE")
    if not path:
        return storage.load_global(FILE, {})
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data) -> None:
    path = os.getenv("JARVIS_USAGE_FILE")
    if not path:
        return storage.save_global(FILE, data)
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def budget() -> float:
    return float(os.getenv("JARVIS_MONTHLY_BUDGET", "20"))


def record(model: str, usage, *, prices=None, component="conversation") -> float:
    with _lock:
        return _record(model, usage, prices=prices, component=component)


def _record(model, usage, *, prices=None, component="conversation"):
    """Add one API response's usage. Returns its cost in USD."""
    p_in, p_out, p_read, p_write = prices or PRICES.get(model, PRICES["claude-sonnet-5-5"])
    get = lambda name: getattr(usage, name, 0) or 0  # noqa: E731
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    cost = (get("input_tokens") * p_in + get("output_tokens") * p_out
            + get("cache_read_input_tokens") * p_read
            + get("cache_creation_input_tokens") * p_write) / 1e6 + searches * WEB_SEARCH_PRICE

    data = _load()
    for key in (date.today().strftime("%Y-%m"), date.today().isoformat()):
        entry = data.setdefault(key, {"cost": 0.0, "requests": 0, "input": 0, "output": 0,
                                      "cache_read": 0, "searches": 0})
        entry["cost"] = round(entry["cost"] + cost, 6)
        entry["requests"] += 1
        entry["input"] += get("input_tokens") + get("cache_creation_input_tokens")
        entry["output"] += get("output_tokens")
        entry["cache_read"] += get("cache_read_input_tokens")
        entry["searches"] += searches
        parts = entry.setdefault("components", {})
        part = parts.setdefault(component, {"cost": 0.0, "requests": 0})
        part["cost"] = round(part["cost"] + cost, 6)
        part["requests"] += 1
    _save(data)
    return cost


def spent(period: str = "month") -> float:
    key = date.today().strftime("%Y-%m") if period == "month" else date.today().isoformat()
    return _load().get(key, {}).get("cost", 0.0)


def over_budget() -> bool:
    return spent("month") >= budget()


def spent_since(day: str) -> float:
    """Everything spent on or after `day` (YYYY-MM-DD), from the daily rows."""
    return round(sum(v.get("cost", 0.0) for k, v in _load().items() if len(k) == 10 and k >= day and isinstance(v, dict)), 6)


def summary() -> str:
    data = _load()
    month = data.get(date.today().strftime("%Y-%m"), {})
    today = data.get(date.today().isoformat(), {})
    b = budget()
    m = month.get("cost", 0.0)
    return (f"Today: ${today.get('cost', 0):.2f} over {today.get('requests', 0)} requests. "
            f"This month: ${m:.2f} of the ${b:.2f} budget ({m / b * 100:.0f}%), "
            f"{month.get('requests', 0)} requests, {month.get('searches', 0)} web searches.")
