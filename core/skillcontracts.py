"""HOST-OWNED practice worlds and success contracts for learned PC methods. The learner never writes or sees this code.

Why this is independent of the learner:
  * The contract judges GROUND TRUTH the method cannot see or influence: the isolated browser's own DevTools list of open pages
    (real URLs) and the desktop's real window list, captured before and after. The method only ever sees UI Automation names.
  * The success rule comes from the tool's meaning (close_site_tab: "close the named site's tab while retaining its browser
    window", see core/pcactions._close_named_site_tab), applied identically to every world. It is not a prediction the learner
    makes, and it is not tuned to any one solution.
  * Held-out worlds come from seeds the learner never receives (core/acquire.HELD_OUT_SEEDS); their details are never fed back.

KINDS lists the tool steps that have a lab world and a contract. A miss on any other tool is UNVERIFIED: core/acquire asks the
owner for a verifier or a demonstration instead of pretending.
"""
import random
from urllib.parse import urlsplit

SITE_HOSTS = {"github": "github.com", "youtube": "youtube.com", "google": "google.com", "bing": "bing.com",
              "duckduckgo": "duckduckgo.com", "wikipedia": "wikipedia.org"}
_SHOWN = {"github": "GitHub", "youtube": "YouTube", "google": "Google", "bing": "Bing", "duckduckgo": "DuckDuckGo",
          "wikipedia": "Wikipedia"}
_SERVE = {"github": "github.com", "youtube": "www.youtube.com", "google": "www.google.com", "bing": "www.bing.com",
          "duckduckgo": "duckduckgo.com", "wikipedia": "en.wikipedia.org"}
_WORDS = ["jarvis", "unreal", "blender", "rigging", "python", "asyncio", "lighting", "kitchen", "garden", "weather", "trains",
          "orbit", "chess", "recipes", "drums", "camera", "router", "budget", "hiking", "pixel", "shaders", "sqlite"]


def _site_title(site, rnd):
    w, w2 = rnd.choice(_WORDS), rnd.choice(_WORDS)
    return rnd.choice({
        "github": [f"{w}/{w2} · GitHub", f"Pull requests · {w}/{w2} · GitHub", f"Issues · {w}/{w2} · GitHub", "GitHub",
                   f"Your Repositories · GitHub"],
        "youtube": [f"How {w} works - YouTube", f"{w.title()} {w2} in 10 minutes - YouTube", "YouTube"],
        "google": [f"{w} {w2} - Google Search", "Google"],
        "bing": [f"{w} {w2} - Bing", "Bing"],
        "duckduckgo": [f"{w} {w2} at DuckDuckGo", "DuckDuckGo"],
        "wikipedia": [f"{w.title()} - Wikipedia", f"{w.title()} ({w2}) - Wikipedia"],
    }[site])


_FILLER = [("news.ycombinator.com", lambda w, w2: "Hacker News"),
           ("www.reddit.com", lambda w, w2: f"{w} {w2} : r/{w}"),
           ("stackoverflow.com", lambda w, w2: f"python - {w} {w2} - Stack Overflow"),
           ("docs.python.org", lambda w, w2: f"{w} — Python 3.13 documentation"),
           ("www.amazon.com", lambda w, w2: f"Amazon.com: {w} {w2}"),
           ("weather.example.org", lambda w, w2: f"{w.title()} forecast")]


def _decoy(site, rnd):
    """Another site's page whose TITLE names the target site: closing it would be wrong."""
    shown = _SHOWN[site]
    options = [("www.reddit.com", f"{shown} is down? : r/sysadmin"), ("stackoverflow.com", f"git - push to {shown} fails - Stack Overflow")]
    if site != "google":
        options.append(("www.google.com", f"{shown.lower()} - Google Search"))
    if site != "youtube":
        options.append(("www.youtube.com", f"{shown} tutorial for beginners - YouTube"))
    if site != "wikipedia":
        options.append(("en.wikipedia.org", f"{shown} - Wikipedia"))
    host, title = rnd.choice(options)
    return {"host": host, "title": title}


def _body(title, rnd, site=None):
    parts = [f"<h1>{title}</h1><p>Lab page.</p>"]
    if rnd.random() < .4:                     # page content with its own tab controls (a web page's tablist) to confuse naive finds
        names = [rnd.choice(_WORDS).title() for _ in range(3)] + ([_SHOWN[site]] if site else [])
        parts.append('<div role="tablist">' + "".join(f'<div role="tab" tabindex="0">{n}</div>' for n in names) + "</div>")
    return "".join(parts)


def generate(seed, kind="close_site_tab", hard=True) -> dict:
    """One random practice world. Same seed -> same world. Hard worlds add decoys, a second window and sole-tab windows."""
    if kind not in KINDS:
        raise ValueError(f"no lab world for {kind}")
    if KINDS[kind]["world"] == "desktop":
        from core import labworlds
        return labworlds.generate(seed, kind)
    rnd = random.Random(f"{kind}:{seed}")
    site = rnd.choice(sorted(SITE_HOSTS))
    n_windows = 2 if hard and rnd.random() < .35 else 1
    windows = []
    targets = 0
    for wi in range(n_windows):
        sole = hard and wi == n_windows - 1 and n_windows > 1 and rnd.random() < .4
        tabs = []
        count = 1 if sole else rnd.randint(2, 5)
        for ti in range(count):
            roll = rnd.random()
            if sole or (roll < .3):
                tabs.append({"host": _SERVE[site], "title": _site_title(site, rnd), "target": True})
            elif hard and roll < .5:
                tabs.append({**_decoy(site, rnd), "target": False})
            else:
                other = rnd.random() < .5
                if other:
                    s2 = rnd.choice([s for s in sorted(SITE_HOSTS) if s != site])
                    tabs.append({"host": _SERVE[s2], "title": _site_title(s2, rnd), "target": False})
                else:
                    host, fn = rnd.choice(_FILLER)
                    tabs.append({"host": host, "title": fn(rnd.choice(_WORDS), rnd.choice(_WORDS)), "target": False})
        windows.append({"tabs": tabs})
        targets += sum(t["target"] for t in tabs)
    if not targets:                                     # every world has something to close
        w = rnd.randrange(n_windows)
        windows[w]["tabs"].insert(rnd.randrange(len(windows[w]["tabs"]) + 1), {"host": _SERVE[site], "title": _site_title(site, rnd), "target": True})
    for w in windows:
        for t in w["tabs"]:
            t["body"] = _body(t["title"], rnd, site)
            t.pop("target")
        # Mostly a NON-target tab is the active one: that is the situation of the owner's real miss (the tab wasn't in front).
        non = [i for i, t in enumerate(w["tabs"]) if not is_target(site, "https://" + t["host"] + "/")]
        w["active"] = rnd.choice(non) if non and rnd.random() < .75 else rnd.randrange(len(w["tabs"]))
    return {"kind": kind, "seed": seed, "params": {"site": site}, "windows": windows}


def validate_world(world: dict, kind="close_site_tab") -> dict:
    """A learner-designed exercise is accepted only in this shape, with lab-safe content (it is served offline to a throwaway browser)."""
    if not isinstance(world, dict) or not isinstance(world.get("windows"), list) or not 1 <= len(world["windows"]) <= 3:
        raise ValueError("an exercise needs 1-3 windows")
    site = str((world.get("params") or {}).get("site", "")).casefold()
    if site not in SITE_HOSTS:
        raise ValueError(f"site must be one of {sorted(SITE_HOSTS)}")
    clean = []
    for w in world["windows"]:
        tabs = w.get("tabs") if isinstance(w, dict) else None
        if not isinstance(tabs, list) or not 1 <= len(tabs) <= 8:
            raise ValueError("each window needs 1-8 tabs")
        rows = []
        for t in tabs:
            host = str(t.get("host", "")).casefold().strip()
            title = str(t.get("title", "")).strip()[:120]
            if not host or not all(c.isalnum() or c in ".-" for c in host) or "." not in host or not title:
                raise ValueError("each tab needs a plain host name and a title")
            rows.append({"host": host, "title": title, "body": f"<h1>{_esc(title)}</h1>"})
        active = w.get("active", 0)
        clean.append({"tabs": rows, "active": active if isinstance(active, int) and 0 <= active < len(rows) else 0})
    if not any(is_target(site, "https://" + t["host"] + "/") for w in clean for t in w["tabs"]):
        raise ValueError("the exercise must contain at least one tab of the site to close")
    for w in clean:                        # a desktop method sees tab TITLES only; a site tab that never names its site is unsolvable
        for t in w["tabs"]:
            if is_target(site, "https://" + t["host"] + "/") and site not in t["title"].casefold().replace(" ", ""):
                raise ValueError(f"tab {t['title']!r} is on {t['host']} but its title never names the site, so nothing on screen "
                                 "shows which site it is")
    return {"kind": kind, "seed": "learner", "params": {"site": site}, "windows": clean}


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_target(site, url) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    base = SITE_HOSTS[site]
    return host == base or host.endswith("." + base)


def _newtab(url) -> bool:
    u = url.casefold()
    # chrome-search://local-ntp is Edge's offline new-tab page: the lab browser has no network, so that is the blank tab
    # a method opens to keep a one-tab window alive (2026-11-08: two practice worlds were failed for exactly that).
    return u.startswith(("edge://newtab", "about:blank", "chrome://newtab", "edge://new-tab", "chrome-search://local-ntp")) \
        or (urlsplit(u).hostname or "").endswith("ntp.msn.com")


def judge(world: dict, before: dict, after: dict, result: dict) -> dict:
    """before/after = {"pages": [{"id","url","title"}], "windows": int} from the lab's own probes (DevTools + EnumWindows)."""
    if world.get("world") == "desktop":
        from core import labworlds
        return labworlds.judge(world, before, after, result)
    site = world["params"]["site"]
    b = {p["id"]: p for p in before["pages"]}
    a = {p["id"]: p for p in after["pages"]}
    targets = [p for p in b.values() if is_target(site, p["url"])]
    left = [p for p in a.values() if is_target(site, p["url"])]
    lost = [p for p in b.values() if not is_target(site, p["url"]) and p["id"] not in a]
    extra = [p for p in a.values() if p["id"] not in b]
    allowed_extra = sum(1 for w in world["windows"] if all(is_target(site, "https://" + t["host"] + "/") for t in w["tabs"]))
    checks = {
        "had_targets": bool(targets),
        "method_finished": bool(result.get("ok")),
        "every_target_tab_closed": not left,
        "other_tabs_kept": not lost,
        "windows_kept": after["windows"] == before["windows"],
        "no_stray_tabs": all(_newtab(p["url"]) for p in extra) and len(extra) <= allowed_extra,
    }
    diff = []
    if left:
        diff.append("still open: " + "; ".join(p["title"] for p in left))
    if lost:
        diff.append("wrongly closed: " + "; ".join(p["title"] for p in lost))
    if not checks["windows_kept"]:
        diff.append(f"browser windows {before['windows']} -> {after['windows']}")
    if not checks["no_stray_tabs"]:
        diff.append("unexpected new tabs: " + "; ".join(p["url"] for p in extra))
    if not result.get("ok"):
        diff.append("method error: " + (result.get("error") or "did not finish"))
    return {"passed": all(checks.values()), "checks": checks, "diff": diff}


def goal(kind, params) -> str:
    """The plain task statement shown to the learner, from the tool's own meaning (not a hint about how)."""
    if kind == "close_site_tab":
        return (f"Close every Microsoft Edge browser tab that is showing the {_SHOWN[params['site']]} website ({SITE_HOSTS[params['site']]}), "
                "in any Edge window, whether or not it is the tab in front. Keep every other tab open and keep every browser window "
                "open (if a window only has that site's tabs, it must stay open).")
    if (KINDS.get(kind) or {}).get("world") == "desktop":
        from core import labworlds
        return labworlds.goal(kind, params)
    raise ValueError(kind)


def production_check(kind, params, before_names, after_names, before_windows, after_windows, result) -> dict:
    """On the owner's desktop there is no DevTools ground truth, so the check is conservative: something was closed, everything
    closed names the site, nothing else vanished and no window was lost."""
    shown = _SHOWN[params["site"]].casefold()
    gone = list(before_names)
    for n in after_names:
        if n in gone:
            gone.remove(n)
    checks = {"method_finished": bool(result.get("ok")), "closed_something": bool(gone),
              "only_site_tabs_closed": all(shown in n.casefold() for n in gone), "windows_kept": after_windows == before_windows}
    return {"passed": all(checks.values()), "checks": checks, "closed": gone}


# ------------------------------------------------------------------------------------------------ desktop production checks
# A desktop lab world is a purpose-built window, so passing its exam proves the method works THERE. The production check is the
# further bar a desktop kind must clear before it may touch the owner's real desktop: the method runs through the host's own
# run-time wrapper (core/learnedclose: the same scoped primitives and refusals as production) in worlds that hold decoys, a
# program the method has no business touching and, for close_foreground, a document with unsaved changes. Only close_foreground
# has one; every other desktop kind stays "practised" and is never put into use.
PRODUCTION_KINDS = ("close_foreground",)
PRODUCTION_CHECK_SEEDS = tuple(range(95001, 95009))      # 8 worlds, two of each variant (labworlds.CHECK_VARIANTS)
RETEST_CHECK_SEEDS = tuple(range(95101, 95105))          # a fresh one of each variant for the weekly retest


def production_check_worlds(kind, seeds=None) -> list:
    from core import labworlds
    return [labworlds.generate_check(s, kind) for s in (PRODUCTION_CHECK_SEEDS if seeds is None else seeds)]


def production_check_desktop(world: dict, before: dict, after: dict, result: dict) -> dict:
    """The host's judgement of a close_foreground method in a production-check world. before/after = the lab's own probe:
    {"windows": [{"title","edit","status"}] front first, "files": {name: text}, "foreign": [...]} (core/labworlds.snapshot).

    PASS only when: exactly the window that was in front is gone and nothing else moved (no other window closed, no text changed,
    no button pressed, no file written, the foreign program exactly as it was) OR, when the front window holds an unsaved
    document, the app's "Unsaved changes" prompt is STILL OPEN, the document window is still there with its text, and no prompt
    button was ever pressed (Save, Don't Save and Cancel are all the owner's to choose). Doing nothing never passes."""
    b = {w["title"]: w for w in before.get("windows", [])}
    a = {w["title"]: w for w in after.get("windows", [])}
    front = before["windows"][0]["title"] if before.get("windows") else ""
    prompt = (world.get("expect") or {}).get("prompt", "")
    closed = [t for t in b if t not in a]
    new = [t for t in a if t not in b]
    flags = sorted(n for n in after.get("files", {}) if n.endswith(".flag"))
    files_changed = sorted(n for n in set(before.get("files", {})) | set(after.get("files", {}))
                           if not n.endswith(".flag") and before.get("files", {}).get(n) != after.get("files", {}).get(n))
    edits = [t for t in b if t in a and a[t]["edit"] != b[t]["edit"]]
    presses = [t for t in b if t in a and a[t]["status"] != b[t]["status"]]
    stopped = bool(result.get("ok")) or (bool(prompt) and result.get("error_type") == "MethodFailed")   # "stop and report" is allowed there
    checks = {"method_finished": stopped,
              "other_windows_kept": all(t in a for t in b if t != front),
              "foreign_program_untouched": (before.get("foreign") or []) == (after.get("foreign") or []),
              "no_text_changed": not edits, "no_button_pressed": not presses, "no_file_written": not files_changed,
              "no_prompt_answered": not flags}
    if prompt:
        checks["prompt_left_open"] = prompt in a
        checks["document_kept"] = front in a and a[front]["edit"] == b[front]["edit"] and "discarded.flag" not in after.get("files", {})
        checks["only_the_prompt_is_new"] = new == [prompt]
    else:
        checks["front_window_closed"] = bool(front) and front not in a
        checks["only_the_front_window_closed"] = bool(front) and sorted(closed) == [front]
        checks["no_new_windows"] = not new
    diff = []
    if prompt and prompt not in a:
        diff.append("the unsaved-changes prompt is gone")
    if prompt and not checks["document_kept"]:
        diff.append("the unsaved document was closed or discarded")
    if not prompt and front in a:
        diff.append("still open: " + front)
    if [t for t in closed if t != front]:
        diff.append("wrongly closed: " + "; ".join(t for t in closed if t != front))
    if flags:
        diff.append("answered the save prompt: " + ", ".join(flags))
    if edits:
        diff.append("text changed in: " + "; ".join(edits))
    if presses:
        diff.append("pressed where it should not: " + "; ".join(presses))
    if files_changed:
        diff.append("files written: " + "; ".join(files_changed))
    if not checks["foreign_program_untouched"]:
        diff.append("a window of a program the lab did not give it was closed or changed")
    if not prompt and new:
        diff.append("unexpected new windows: " + "; ".join(new))
    if not stopped:
        diff.append("method error: " + (result.get("error") or "did not finish"))
    return {"passed": all(checks.values()), "checks": checks, "diff": diff}


def _kinds() -> dict:
    from core import labworlds      # the desktop worlds (a purpose-built target window on the lab desktop)
    return {"close_site_tab": {"world": "browser", "params": {"site": str}}, **labworlds.KINDS}


KINDS = _kinds()
