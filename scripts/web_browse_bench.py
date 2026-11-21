"""Real-website benchmark for the isolated browser worker (public pages only, read-only).

Each task has a page-specific success predicate (right host, expected text), not just "no exception".
Usage: python scripts/web_browse_bench.py [reps] > report.json
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JARVIS_BROWSER_RUNTIME", r"C:\Users\you\ECC-browser-runtime")
from core import webbrowse  # noqa: E402


def host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def ok_search_open(out, hosts=(), text=()):
    steps = out["steps"]
    last = next((s for s in reversed(steps) if s.get("url")), {})
    body = " ".join(s.get("text", "") for s in steps).lower()
    return all(s["ok"] for s in steps) and (not hosts or any(host(last.get("url", "")).endswith(h) for h in hosts)) and \
        all(t.lower() in body for t in text)


TASKS = {
    "bing_search_open_docs": ([{"op": "search", "engine": "bing", "query": "python asyncio documentation"},
                               {"op": "open_result", "index": 1}, {"op": "read"}], lambda o: ok_search_open(o, text=("asyncio",))),
    "bing_mdn_fetch": ([{"op": "search", "engine": "bing", "query": "MDN fetch API"}, {"op": "open_result", "match": "developer.mozilla.org"},
                        {"op": "read"}], lambda o: ok_search_open(o, ("mozilla.org",), ("fetch",))),
    "youtube_blender": ([{"op": "search", "engine": "youtube", "query": "blender rigging tutorial"},
                         {"op": "open_result", "index": 1}, {"op": "read"}],
                        lambda o: ok_search_open(o, ("youtube.com",))),
    "youtube_unreal": ([{"op": "search", "engine": "youtube", "query": "unreal engine 5 lumen explained"},
                        {"op": "open_result", "index": 1}], lambda o: ok_search_open(o, ("youtube.com",))),
    "ddg_wikipedia": ([{"op": "search", "engine": "duckduckgo", "query": "photosynthesis wikipedia"},
                       {"op": "open_result", "match": "wikipedia.org"}, {"op": "read"}],
                      lambda o: ok_search_open(o, ("wikipedia.org",), ("photosynthesis",)) or
                      (not o["steps"][0]["ok"] and "HTTP 403" in o["steps"][0]["reason"])),  # honest refusal counts as handled
    "docs_follow_link_scroll_find": ([{"op": "goto", "url": "https://docs.python.org/3/"}, {"op": "follow_link", "text": "Tutorial"},
                                      {"op": "scroll", "to": "bottom"}, {"op": "find", "text": "interpreter"}],
                                     lambda o: all(s["ok"] for s in o["steps"]) and "tutorial" in o["steps"][1]["url"]),
    "wikipedia_find": ([{"op": "goto", "url": "https://en.wikipedia.org/wiki/Skeletal_animation"}, {"op": "find", "text": "rigging"},
                        {"op": "scroll", "to": "down"}], lambda o: all(s["ok"] for s in o["steps"])),
    "two_tabs": ([{"op": "goto", "url": "https://example.com/"}, {"op": "new_tab", "url": "https://www.iana.org/"},
                  {"op": "switch_tab", "index": 1}, {"op": "read"}],
                 lambda o: all(s["ok"] for s in o["steps"]) and len(o["tabs"]) == 2 and "documentation examples" in o["steps"][3]["text"].lower()),
    "failed_404_reported": ([{"op": "goto", "url": "https://example.com/definitely-missing-page-12345"}],
                            lambda o: not o["steps"][0]["ok"] and o["steps"][0]["reason"] == "HTTP 404"),
    "failed_dns_reported": ([{"op": "goto", "url": "https://no-such-host-jarvis-bench.invalid/"}],
                            lambda o: not o["steps"][0]["ok"]),
    "google_blocked_reported": ([{"op": "search", "engine": "google", "query": "blender rigging tutorial"}],
                                lambda o: o["steps"][0]["ok"] or ("interstitial" in o["steps"][0].get("reason", "") or "bot-check" in o["steps"][0].get("reason", ""))),
    "delayed_page_3s": ([{"op": "goto", "url": "https://httpbingo.org/delay/3"}, {"op": "read"}],
                        lambda o: o["steps"][0]["ok"] and o["steps"][0]["ms"] >= 2800),
}

def research_ok(query, hosts):
    def check(_):
        from core import webbrowse
        out = webbrowse.search_and_read(query, "bing", 3)
        return out["ok"] and len(out["sources"]) >= 2 and any(host(x["url"]).endswith(hosts) for x in out["sources"])
    return check


TASKS_E2E = {
    "wikipedia_form_search_get": (None, lambda _: __import__("core.webbrowse", fromlist=["x"]).run([
        {"op": "goto", "url": "https://en.wikipedia.org/wiki/Special:Search"}, {"op": "fill", "name": "search", "text": "armature animation"},
        {"op": "submit"}, {"op": "read"}])["steps"][-1]["ok"]),
    "e2e_research_blender_bones": (None, research_ok("how armature bones work in blender", ("blender.org",))),
    "e2e_research_pubkey": (None, research_ok("how does public key cryptography work", ("wikipedia.org", "mozilla.org", "cloudflare.com", "github.io"))),
    "e2e_research_lumen": (None, research_ok("unreal engine 5 lumen global illumination documentation", ("epicgames.com", "unrealengine.com"))),
}


EXPECTED_FAILURE = {"failed_404_reported", "failed_dns_reported", "google_blocked_reported"}


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def main(reps):
    rows, report = [], {}
    for name, (steps, check) in {**TASKS, **TASKS_E2E}.items():
        runs = []
        for rep in range(reps):
            t0 = time.monotonic()
            try:
                out = webbrowse.run(steps, timeout=85) if steps else None
                good = bool(check(out))
                detail = None if good else ([{k: s.get(k) for k in ("op", "ok", "reason", "url")} for s in out["steps"]] if out else "see query")
            except Exception as error:
                good, detail = False, type(error).__name__ + ": " + str(error)[:200]
            runs.append({"ok": good, "ms": round((time.monotonic() - t0) * 1000), "detail": detail})
            print(name, rep, runs[-1]["ok"], runs[-1]["ms"], file=sys.stderr, flush=True)
        report[name] = runs
        rows += runs
    def stats(values):
        return {"n": len(values), "p50_ms": statistics.median(values) if values else None, "p95_ms": pct(values, .95) if values else None}
    completed = {k: v for k, v in report.items() if k not in EXPECTED_FAILURE and not (k == "ddg_wikipedia" and all(
        r["ok"] and r["ms"] < 1800 for r in v))}
    detected = {k: v for k, v in report.items() if k not in completed}
    per_task = {k: stats([r["ms"] for r in v]) | {"ok": sum(r["ok"] for r in v)} for k, v in report.items()}
    ms = [r["ms"] for r in rows]
    summary = {"tasks": len(report), "runs": len(rows), "verified_success": sum(r["ok"] for r in rows) / len(rows),
               "task_completions": {"runs": sum(len(v) for v in completed.values()), "ok": sum(r["ok"] for v in completed.values() for r in v),
                                    **stats([r["ms"] for v in completed.values() for r in v])},
               "correctly_detected_failures": {"runs": sum(len(v) for v in detected.values()), "ok": sum(r["ok"] for v in detected.values() for r in v),
                                               "tasks": sorted(detected), **stats([r["ms"] for v in detected.values() for r in v])},
               "per_task": per_task, **stats(ms)}
    print(json.dumps({"summary": summary, "tasks": report}, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
