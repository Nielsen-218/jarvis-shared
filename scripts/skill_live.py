"""LIVE correctness check of Jarvis's online skills against the REAL services — read-only.

skill_smoke.py proves every skill runs and fails gracefully OFFLINE. This proves the online ones actually WORK:
each allowlisted skill is called for real and its output is checked for the right content (a temperature for the
weather, distance + duration for directions, prices for grocery deals, real headlines for a feed...).

Read-only by construction: only skills on the ALLOWLIST below run, and none of them downloads, deletes, restarts,
rescans, adds or changes anything. Data and the memory vault go to a temp folder; webbrowser / os.startfile are
stubbed so nothing opens on screen. Free services only (no paid AI/vision). OpenStreetMap calls are spaced 1.2 s
apart per Nominatim's usage policy.

Verdicts:  PASS  correct content      FAIL  ran but the content is wrong/missing (a real defect)
           UNAVAILABLE  the service is down / not configured and the skill SAID so clearly (not a defect)

Run:  python scripts/skill_live.py [--json out.json] [--only a,b]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEPART = (date.today() + timedelta(days=40)).isoformat()
OSM = {"directions", "find_places", "shopping_route", "get_weather"}   # space these out (Nominatim policy)


def has(*patterns, flags=re.I):
    """Check: every regex must match the output."""
    def check(out: str):
        missing = [p for p in patterns if not re.search(p, out, flags)]
        return (not missing, f"missing {missing}" if missing else "ok")
    return check


def lines_at_least(n: int, *also):
    def check(out: str):
        rows = [ln for ln in out.splitlines() if ln.strip()]
        ok, why = has(*also)(out) if also else (True, "ok")
        return (len(rows) >= n and ok, f"{len(rows)} lines (need {n}); {why}")
    return check


# skill -> (args, check). Every one of these is READ-ONLY.
ALLOWLIST = {
    "get_weather":          ({"place": "Fayetteville, NC", "days": 2}, has(r"-?\d+\s*°", r"Fayetteville")),
    "directions":           ({"origin": "Fayetteville, NC", "destination": "Raleigh, NC"},
                             has(r"\d+(\.\d+)?\s*(mi|miles|km)", r"\d+\s*(min|minutes|h\b|hr|hour)")),
    "find_places":          ({"query": "coffee", "near": "Fayetteville, NC"}, lines_at_least(2)),
    "shopping_route":       ({"stores": ["Walmart", "ALDI"]},
                             has(r"min of driving", r"miles round trip", r"1\. (Walmart|ALDI): ", r"google\.com/maps/dir")),
    "grocery_deals":        ({"items": "eggs", "zip_code": "28314"}, has(r"\$\d")),
    "flight_search":        ({"origin": "FAY", "destination": "MIA", "depart": DEPART},
                             has(r"google\.com/travel/flights", r"kayak\.com/flights/FAY-MIA/" + DEPART, r"skyscanner")),
    "movies_out":           ({"which": "in theaters"}, lines_at_least(3)),
    "movie_info":           ({"title": "Inception"}, has(r"Inception", r"2010")),
    "search_web_free":      ({"query": "Open-Meteo free weather forecast API documentation"}, has(r"https?://")),
    "read_web_pages":       ({"urls": ["https://example.com"]}, has(r"Example Domain")),
    "read_feed":            ({"url": "https://feeds.bbci.co.uk/news/rss.xml", "count": 5}, lines_at_least(5)),
    "youtube_transcript":   ({"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}, has(r"zoo", r"elephant")),
    "public_domain_search": ({"query": "Nosferatu 1922"}, has(r"nosferatu")),
    "homelab_status":       ({}, has(r"## apps", r"## drive", r"## pi")),
    "homelab_health":       ({}, has(r"all good|problem|down|unhealthy|mount|error")),
    "list_folder":          ({"folder": ""}, has(r"movies|anime|downloads|photos|files")),
    "find_small_videos":    ({"folder": "downloads", "max_mb": 50}, has(r"MB|No small video files")),
    "jellyfin_overview":    ({}, has(r"\d")),
    "jellyfin_search":      ({"query": "the"}, lines_at_least(1)),
    "qbit_list":            ({}, lines_at_least(1)),
    "briefing_info":        ({}, lines_at_least(2)),
    "weekly_report":        ({}, lines_at_least(1)),
}

UNAVAILABLE = re.compile(r"isn't reachable|not configured|isn't connected|rejected Jarvis's login|needs fixing|"
                         r"rate-limiting|having problems on its end|couldn't reach", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = {s.strip() for s in a.only.split(",") if s.strip()}

    tmp = Path(tempfile.mkdtemp(prefix="jarvis_skill_live_"))
    import webbrowser
    opened = []
    for fn in ("open", "open_new", "open_new_tab"):
        setattr(webbrowser, fn, lambda url, *x, **k: opened.append(url) or True)
    if hasattr(os, "startfile"):
        os.startfile = lambda p, *x, **k: opened.append(str(p))             # type: ignore[assignment]
    from core import approvals, storage, users
    storage.DATA = tmp
    os.environ["JARVIS_VAULT"] = str(tmp / "vault")
    users.set_current("owner")
    from tools import load_all, registry
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        load_all()

    # Seed only what a skill needs to take its real path (into the TEMP data, never the real household).
    SETUP = {"shopping_route": lambda: __import__("tools.kitchen", fromlist=["x"]).set_household_food(
        "zip", os.getenv("JARVIS_LIVE_ZIP", "62701"))}

    report, last_osm = {}, 0.0
    for name, (args, check) in ALLOWLIST.items():
        if only and name not in only:
            continue
        if name in SETUP:
            SETUP[name]()
        if name in OSM:
            time.sleep(max(0.0, 1.2 - (time.time() - last_osm)))
            last_osm = time.time()
        approvals._pending.clear(); approvals._direct.clear()
        t0 = time.perf_counter()
        res, is_err = registry.run(name, args)
        dt = round(time.perf_counter() - t0, 2)
        out = res if isinstance(res, str) else json.dumps(res)[:2000]
        if is_err and UNAVAILABLE.search(out):
            verdict, why = "UNAVAILABLE", "service down/not configured, and the skill said so clearly"
        else:
            ok, why = check(out)
            verdict = "PASS" if ok and not is_err else "FAIL"
            if is_err:
                why = f"error result: {why}"
        report[name] = {"verdict": verdict, "why": why, "seconds": dt, "args": args, "result": out[:600]}
        print(f"{verdict:11} {dt:6.2f}s  {name:21} {why[:70]}")
    report["_opened_on_screen_blocked"] = opened
    if a.json:
        a.json.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    fails = [n for n, r in report.items() if isinstance(r, dict) and r.get("verdict") == "FAIL"]
    print(json.dumps({"pass": sum(1 for r in report.values() if isinstance(r, dict) and r.get("verdict") == "PASS"),
                      "fail": len(fails), "unavailable": sum(1 for r in report.values() if isinstance(r, dict)
                                                              and r.get("verdict") == "UNAVAILABLE")}))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
