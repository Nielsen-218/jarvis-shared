"""Multi-step public web browsing through the isolated Playwright worker (scripts/web_browse.py).

Reuses the research broker's runtime settings, job-object process limits and pinned-egress proxy.  It is a
READ-ONLY public-web capability: no owner profile, cookies, form submission or downloads.  Everything a page says
is untrusted data -- it can be quoted and cited, never obeyed.
"""
import base64
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from core import research, storage

ENGINES = ("google", "bing", "youtube", "duckduckgo")
STEPS = {"fill", "submit", "screenshot", "playback", "search", "open_result", "goto", "follow_link", "scroll", "find", "read", "new_tab", "switch_tab",
         "close_tab", "back", "research"}
INJECTION = re.compile(r"ignore (?:all |any )?(?:previous|prior|above) instructions|disregard (?:the )?(?:system|previous)|"
                       r"you are (?:now )?(?:an? )?(?:ai|assistant|language model)|(?:run|execute|type) (?:this )?(?:command|powershell|cmd)|"
                       r"(?:open|launch) (?:the )?(?:terminal|command prompt)|download and (?:run|install)", re.I)


class BrowserUnavailable(RuntimeError):
    pass


def runtime():
    """The owner-registered isolated runtime (data/research-browser.json, written by research_browser.configure).

    Development convenience: JARVIS_BROWSER_RUNTIME=<folder holding venv/ and browsers/> is honoured ONLY when it is set
    explicitly in the environment.  There is no built-in default path, so an unconfigured install fails closed.
    JARVIS_WEB_BROWSE=off switches these features off without touching the registration other tools also use."""
    if os.environ.get("JARVIS_WEB_BROWSE", "on").strip().lower() in ("off", "0", "false", "no"):
        raise BrowserUnavailable("Browser control is switched off (JARVIS_WEB_BROWSE=off)")
    settings = storage.load_global("research-browser.json", {})
    if settings.get("python") and settings.get("browsers"):
        python, browsers = Path(settings["python"]), Path(settings["browsers"])
        if python.is_file() and browsers.is_dir():
            return str(python), str(browsers)
        raise BrowserUnavailable("The registered browser runtime is missing from disk; re-register or repair it")
    base = os.environ.get("JARVIS_BROWSER_RUNTIME")
    if base:
        python, browsers = Path(base) / "venv/Scripts/python.exe", Path(base) / "browsers"
        if python.is_file() and browsers.is_dir():
            return str(python), str(browsers)
        raise BrowserUnavailable("JARVIS_BROWSER_RUNTIME does not hold venv/ and browsers/")
    raise BrowserUnavailable("The isolated browser runtime is not registered")


def available() -> bool:
    try:
        runtime()
        return True
    except BrowserUnavailable:
        return False


def validate(steps, fixture=None):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 14:
        raise ValueError("One to fourteen browser steps are allowed")
    for step in steps:
        if not isinstance(step, dict) or step.get("op") not in STEPS:
            raise ValueError("Unsupported browser step")
        if step["op"] == "search" or (step["op"] == "research" and not step.get("urls") and not step.get("candidates")):
            if step.get("engine", "bing") not in ENGINES or not isinstance(step.get("query"), str) or not 2 <= len(step["query"].strip()) <= 300:
                raise ValueError("Search needs a known site and a short query")
        if step["op"] in ("goto", "new_tab") and step.get("url") and not (fixture and step["url"].startswith(fixture + "/")):
            research.public_url(step["url"], resolve=False)  # worker egress also re-checks every hop
        if step["op"] == "fill" and not (isinstance(step.get("text"), str) and len(step["text"]) <= 300 and
                                         sum(k in step for k in ("label", "placeholder", "name")) <= 1):
            raise ValueError("Fill needs short text and at most one field selector")
        if step["op"] in ("follow_link", "find") and not (isinstance(step.get("text"), str) and 1 <= len(step["text"]) <= 120):
            raise ValueError("Link/find steps need short text")
    return steps


def run(steps, *, timeout=80, _fixture=None, _fixture_engine=None):
    """Run the steps in one ephemeral browser; returns the worker's structured, untrusted result."""
    validate(steps, _fixture)
    python, browsers = runtime()
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "LOCALAPPDATA", "USERPROFILE"}}
    env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
    command = [python, str(Path(__file__).resolve().parents[1] / "scripts/web_browse.py")]
    if _fixture:  # internal tests only: one exact loopback origin
        p = urlsplit(_fixture)
        if p.scheme != "http" or p.hostname != "127.0.0.1" or not p.port or p.path not in ("", "/") or p.username is not None:
            raise ValueError("Synthetic fixture must be one exact loopback origin")
        command += ["--fixture-origin", _fixture] + (["--fixture-engine", _fixture_engine] if _fixture_engine else [])
    from core.research_process import run as process
    import json
    temp_root = storage.path("research/browser-tmp/.keep").parent.resolve()
    started = time.monotonic()
    # ignore_cleanup_errors: Chromium children can hold a profile file for a moment after the job ended; that must not fail a finished task
    with tempfile.TemporaryDirectory(prefix="web-", dir=temp_root, ignore_cleanup_errors=True) as directory:
        env["TEMP"] = env["TMP"] = directory
        shots = Path(directory) / "shots"
        shots.mkdir()
        code, out, error = process(command, env, timeout, research.stopped,
                                   input_text=json.dumps({"steps": steps, "shot_dir": str(shots)}),
                                   memory_bytes=1536 * 1024 * 1024, process_limit=14)
        images = {str(f): base64.b64encode(f.read_bytes()).decode() for f in shots.glob("*.jpg") if f.stat().st_size < 400_000}
    storage.path("research/browser-runs/" + uuid.uuid4().hex + ".json").write_text(
        json.dumps({"at": time.time(), "exit_code": code, "stderr": error[-4000:]}, indent=2))
    if code:
        raise RuntimeError("Isolated browser failed; inspect private diagnostics")
    result = json.loads(out)
    for step in result["steps"]:
        if step.get("shot"):
            step["shot_b64"] = images.get(step.pop("shot"))
    result["wall_ms"] = round((time.monotonic() - started) * 1000)
    return result


def flag_injection(text):
    """Pages may contain text aimed at an AI agent.  Report it; never act on it."""
    return bool(INJECTION.search(text or ""))


def search_and_read(query, engine="bing", pages=3):
    """Search a site, read the best distinct results in the rendered DOM, and return cited sources."""
    from core import websearch
    result = run([{"op": "research", "engine": engine, "query": query, "pages": pages}])
    step = result["steps"][0]
    if not step["ok"]:
        # The site refused or answered off-topic (headless Google/DuckDuckGo/Bing do).  Use the existing provider chain
        # only to FIND candidate URLs, then still read them in the real browser.
        try:
            found_rows = [{"url": row["url"], "title": row.get("title", ""), "snippet": row.get("snippet", "")}
                          for row in websearch.search(query, limit=8)["results"] if row.get("url", "").startswith("https://")]
        except websearch.SearchUnavailable:
            found_rows = []
        if found_rows:
            again = run([{"op": "research", "query": query, "pages": pages, "candidates": found_rows}])
            again["steps"][0]["log"] = step.get("log", []) + [{"fallback": "provider-chain candidates"}] + again["steps"][0].get("log", [])
            again["wall_ms"] += result["wall_ms"]
            result, step = again, again["steps"][0]
    sources = []
    for row in step.get("sources", []):
        text = row["text"]
        sources.append({"title": row["title"], "url": row["url"], "excerpt": websearch._excerpt(text, query) or text[:400],
                        "facts": websearch.key_lines(text), "found_via": row["found_via"], "rank": row["rank"],
                        "untrusted_instructions_ignored": flag_injection(text)})
    return {"ok": step["ok"], "sources": sources, "log": step.get("log", []), "reason": step.get("reason"),
            "ms": result["wall_ms"], "tabs": result["tabs"]}


def search_pick(query, engine="bing", index=1, match=None, verify_page=True):
    """Search then choose one result.  verify_page=False skips loading it here (YouTube: the visible browser opens it and
    Windows media sessions confirm real playback, so loading it twice would only cost seconds)."""
    pick = {"op": "open_result", **({"match": match} if match else {"index": index})}
    if not verify_page:
        result = run([{"op": "search", "engine": engine, "query": query}])
        step = result["steps"][0]
        rows = step.get("results") or []
        chosen = (next((r for r in rows if match.lower() in (r["title"] + r["url"]).lower()), None) if match else
                  rows[index - 1] if 0 < index <= len(rows) else None)
        if not step["ok"] or not chosen:
            return {"ok": False, "reason": "search: " + str(step.get("reason") or "no such result"), "steps": result["steps"], "ms": result["wall_ms"]}
        return {"ok": True, "url": chosen["url"], "title": chosen["title"], "chosen": chosen, "engine": engine,
                "excerpt": chosen.get("snippet", ""), "ms": result["wall_ms"], "untrusted_instructions_ignored": False,
                "results": rows[:8]}
    result = run([{"op": "search", "engine": engine, "query": query}, pick, {"op": "read"}])
    steps = result["steps"]
    failed = next((s for s in steps if not s["ok"]), None)
    if failed:
        return {"ok": False, "reason": f"{failed['op']}: {failed.get('reason')}", "steps": steps, "ms": result["wall_ms"]}
    chosen = steps[1]["chosen"]
    page = steps[2]
    return {"ok": True, "url": page["url"], "title": page["title"], "chosen": chosen, "engine": engine,
            "excerpt": page["text"][:500], "ms": result["wall_ms"],
            "untrusted_instructions_ignored": flag_injection(page["text"])}


def describe_screenshot(image_b64, question="Read all text visible on this web page and say what the page shows."):
    """Last-resort visual reading for pages whose DOM holds no text (canvas, images).  Local vision model only."""
    from core import intelligence
    reply = intelligence.complete_local({"role": "fast", "tools": [], "messages": [{"role": "user", "content": [
        {"type": "text", "text": question + " Reply with the visible text only, no guesses."},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}}]}]})
    return (reply.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def read_with_vision_fallback(url, question=None):
    """DOM first; one screenshot + local vision only when the rendered DOM text is empty."""
    out = run([{"op": "goto", "url": url}, {"op": "read", "vision": True}])
    page = out["steps"][-1]
    if page["ok"]:
        return {"ok": True, "via": "dom", "url": page["url"], "title": page["title"], "text": page["text"], "ms": out["wall_ms"]}
    if page.get("shot_b64"):
        text = describe_screenshot(page["shot_b64"], question) if question else describe_screenshot(page["shot_b64"])
        return {"ok": len(text) >= 20, "via": "vision", "url": page["url"], "title": page["title"], "text": text, "ms": out["wall_ms"],
                "note": "Read from a screenshot because the page exposed no DOM text; verify before relying on it."}
    return {"ok": False, "via": None, "url": page.get("url"), "reason": page.get("reason"), "ms": out["wall_ms"]}


# ---- short-lived memory of the last result list, so "open the second one" works -----------------------------------
MEMORY_SECONDS = 600
ORDINALS = {"first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def remember(kind, query, rows):
    clean = [{"title": str(r.get("title", ""))[:200], "url": r["url"], "excerpt": str(r.get("excerpt", r.get("snippet", "")))[:600]}
             for r in rows[:8] if str(r.get("url", "")).startswith("https://")]
    storage.save("browse/last.json", {"at": time.time(), "kind": kind, "query": query, "results": clean})


def recall():
    """The last list this person was shown, or None once it is older than ten minutes."""
    data = storage.load("browse/last.json", {})
    if not data.get("results") or time.time() - data.get("at", 0) > MEMORY_SECONDS:
        return None
    return data


def pick(number):
    data = recall()
    if not data:
        raise RuntimeError("I have no recent search results to pick from; ask me to search first.")
    if not 1 <= number <= len(data["results"]):
        raise RuntimeError(f"There are only {len(data['results'])} results from the last search.")
    return data, data["results"][number - 1]
