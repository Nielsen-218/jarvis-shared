"""Trusted isolated multi-step browser worker: DOM-first public web browsing.

Runs a closed vocabulary of deterministic steps (search, open a result, follow a
link, scroll, find text, read, switch tabs) in ONE ephemeral Chromium context
behind the same pinned public-HTTPS egress proxy as research_browser.  No owner
profile, no cookies, no form submission, no model-supplied JavaScript.  Page
text is untrusted data: it is returned to the caller, never obeyed.
"""
import base64
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.browser_proxy import Egress, start

ENGINES = {
    "google": "https://www.google.com/search?q={q}&hl=en",
    "bing": "https://www.bing.com/search?q={q}&setlang=en-US",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={q}",
}
BLOCKED = re.compile(r"unusual traffic|are you a robot|verify (?:that )?you are (?:a )?human|captcha|access denied|"
                     r"request blocked|enable javascript and cookies|attention required|temporarily blocked|"
                     r"sorry, you have been blocked|error 1020", re.I)
TRACKERS = re.compile(r"doubleclick\.net|googlesyndication|google-analytics|googletagmanager|googleadservices|adservice\.google|"
                      r"/youtubei/v1/log_event|/api/stats/|play\.google\.com/log|scorecardresearch|hotjar|segment\.(?:io|com)|"
                      r"connect\.facebook\.net|bat\.bing\.com|clarity\.ms|amplitude\.com|sentry\.io", re.I)
MAX_STEPS, MAX_TABS, MAX_TEXT = 14, 4, 9000
FIXTURE = None  # exact loopback origin, set only by the test-only CLI flag


class StepError(Exception):
    pass


def unwrap(href):
    """Search engines wrap result links; recover the destination URL."""
    p = urlsplit(href)
    q = parse_qs(p.query)
    if p.hostname and p.hostname.endswith("bing.com") and p.path.startswith("/ck/") and q.get("u"):
        raw = q["u"][0]
        if raw.startswith("a1"):
            raw = raw[2:]
            try:
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
            except Exception:
                return href
    if p.hostname and p.hostname.endswith("duckduckgo.com") and q.get("uddg"):
        return unquote(q["uddg"][0])
    if p.hostname and p.hostname.endswith("google.com") and p.path == "/url" and q.get("q"):
        return q["q"][0]
    return href


class Session:
    def __init__(self, context, fixture, page, shot_dir=None):
        self.context, self.fixture, self.tabs, self.current, self.shot_dir = context, fixture, [page], 0, shot_dir
        self.last_results = []

    @property
    def page(self):
        return self.tabs[self.current]


def blocked_reason(page, status, text=True):
    if status in (401, 403, 429):
        return "HTTP %d" % status
    if "/sorry/" in page.url or "consent." in (urlsplit(page.url).hostname or ""):
        return "interstitial"
    if not text:
        return None
    try:
        head = page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 1200)")
    except Exception:
        return None
    if len(head) < 900 and BLOCKED.search(head):
        return "bot-check"
    return None


def dismiss_consent(page):
    """Choose the privacy-preserving option on a consent wall; never accept."""
    for name in (r"reject all", r"decline", r"only necessary", r"reject"):
        try:
            button = page.get_by_role("button", name=re.compile(name, re.I)).first
            if button.is_visible(timeout=400):
                button.click(timeout=2000)
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                return True
        except Exception:
            continue
    return False


def navigate(session, url, text_check=True, timeout_ms=15000):
    """Bounded navigation; one retry only for a server error or a quick network error, never for a timeout.
    Returns facts, never raises on web errors."""
    page, last = session.page, "unknown"
    for attempt in (1, 2):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            status = response.status if response else 0
            if status >= 500 and attempt == 1:
                last = "HTTP %d" % status
                continue
            if "consent." in (urlsplit(page.url).hostname or ""):
                dismiss_consent(page)
            why = blocked_reason(page, status, text_check)
            if status >= 400 or why:
                return {"ok": False, "reason": why or "HTTP %d" % status, "status": status, "url": page.url}
            return {"ok": True, "status": status, "url": page.url, "title": page.title()}
        except Exception as error:
            last = type(error).__name__ + ": " + str(error).splitlines()[0][:160]
            if "Timeout" in type(error).__name__:
                last = "the page did not load in time"
                break  # a slow site is not made faster by asking twice
    return {"ok": False, "reason": last, "status": 0, "url": url}


def extract_results(page, engine):
    if engine == "youtube":
        page.wait_for_selector("ytd-video-renderer a#video-title", state="attached", timeout=9000)
        return page.evaluate("""() => [...document.querySelectorAll('ytd-video-renderer')].slice(0, 15).map(v => {
            const a = v.querySelector('a#video-title');
            const ch = v.querySelector('ytd-channel-name a, #channel-name a');
            const meta = [...v.querySelectorAll('#metadata-line span')].map(s => s.innerText).join(' | ');
            return {title: (a.getAttribute('title') || a.innerText || '').trim(), url: a.href,
                    snippet: ((ch ? ch.innerText + ' | ' : '') + meta).trim(), sponsored: false}; })""")
    selectors = {
        "google": ("#search a:has(h3)", "h3"),
        "bing": ("li.b_algo h2 a", None),
        "duckduckgo": ("a.result__a", None),
    }
    link, _ = selectors[engine]
    page.wait_for_selector(link, state="attached", timeout=9000)
    rows = page.evaluate("""([sel, engine]) => [...document.querySelectorAll(sel)].slice(0, 20).map(a => {
        const box = a.closest('li.b_algo, .result, div.g, div[data-hveid]') || a.parentElement;
        const sn = box ? (box.querySelector('.b_caption p, .result__snippet, div[style*="-webkit-line-clamp"], .VwiC3b, .b_lineclamp2') || {}) : null;
        const h = a.querySelector('h3');
        const ad = !!(box && (box.classList.contains('b_ad') || box.closest('#tads, #tadsb, .b_ad') || a.href.includes('/aclk') || a.href.includes('doubleclick')));
        return {title: ((h ? h.textContent : a.textContent) || '').trim(), url: a.href, snippet: ((sn && sn.textContent) || '').trim(), sponsored: ad}; })""",
                         [link, engine])
    return rows


def clean_results(rows):
    seen, out = set(), []
    for row in rows:
        url = unwrap(row["url"])
        p = urlsplit(url)
        if not (p.scheme == "https" or (FIXTURE and p.scheme + "://" + p.netloc == FIXTURE)) or not p.hostname or row.get("sponsored") or not row.get("title"):
            continue
        host = p.hostname.lower()
        if host.endswith(("google.com", "bing.com", "duckduckgo.com")) and "youtube" not in host:
            continue
        key = (host, p.path.rstrip("/"), p.query if "youtube" in host else "")
        if key in seen:
            continue
        seen.add(key)
        out.append({"rank": len(out) + 1, "title": row["title"][:200], "url": url, "snippet": row.get("snippet", "")[:300]})
    return out


STOP = {"the", "and", "for", "with", "how", "what", "why", "when", "where", "who", "does", "from", "that", "this", "into", "about",
        "are", "was", "can", "you", "your", "work", "works", "use", "using", "best", "new"}


def content_words(query):
    return {w for w in re.findall(r"[a-z0-9]{3,}", query.lower()) if w not in STOP}


def relevant(rows, query):
    """Drop results that are not about the query (headless Bing answered 'armature bones in Blender' with fence and
    title-company sites).  A row needs two content words, or one for a query of three or fewer."""
    words = content_words(query)
    if not words:
        return rows
    need = 1 if len(words) <= 3 else 2
    out = []
    for row in rows:
        tokens = set(re.findall(r"[a-z0-9]+", (row["title"] + " " + row["snippet"] + " " + row["url"]).lower()))
        hits = sum(1 for w in words if any(t == w or (len(w) >= 5 and len(t) >= 5 and t[:5] == w[:5]) for t in tokens))
        if hits >= need:
            out.append(row)
    return out


def score(row, words):
    hay = (row["title"] + " " + row["snippet"] + " " + urlsplit(row["url"]).path).lower()
    hits = sum(1 for w in words if w in hay) + sum(1 for w in words if w in row["title"].lower())
    host = (urlsplit(row["url"]).hostname or "")
    official = (1 if re.search(r"\.(gov|edu|org)$|docs?\.|developer\.|wikipedia", host) else 0) +         (1 if re.search(r"/(docs?|manual|documentation|wiki|reference)/", row["url"].lower()) else 0)
    if re.match(r"(?:staging|dev|test|beta)\.", host.removeprefix("www.")):
        official -= 5
    return hits * 3 + official - row["rank"] * 0.05


def search(session, engine, query):
    base = ENGINES.get(engine)
    if not base:
        raise StepError("Unsupported search site")
    if session.fixture and session.fixture.get("engine_base"):
        base = session.fixture["engine_base"]
    result = navigate(session, base.format(q=quote_plus(query)), text_check=False)
    if not result["ok"]:
        return {"ok": False, "engine": engine, "reason": "search page " + result["reason"], "url": result["url"]}
    try:
        rows = clean_results(extract_results(session.page, engine))
    except Exception as error:
        why = blocked_reason(session.page, 200) or "no results found in the page (" + type(error).__name__ + ")"
        return {"ok": False, "engine": engine, "reason": why, "url": session.page.url}
    words = [w for w in re.findall(r"[a-z0-9]{3,}", query.lower())]
    total = len(rows)
    rows = relevant(rows, query) if engine != "youtube" else rows
    if total and not rows:
        return {"ok": False, "engine": engine, "reason": "off-topic results (%d discarded)" % total, "url": session.page.url}
    rows.sort(key=lambda r: -score(r, words))
    session.last_results = rows
    return {"ok": bool(rows), "engine": engine, "results": rows[:10], "url": session.page.url,
            "reason": None if rows else "the page held no organic results"}


WALK = """() => {
    const out = [];
    const walk = (n) => {
        if (n.nodeType === 3) { const t = n.textContent.trim(); if (t) out.push(t); return; }
        if (n.nodeType !== 1 && n.nodeType !== 11) return;
        if (n.nodeType === 1 && /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|SVG)$/i.test(n.tagName)) return;
        if (n.shadowRoot) walk(n.shadowRoot);
        for (const c of n.childNodes) walk(c);
    };
    walk(document.body || document.documentElement);
    return out.join('\\n');
}"""


def read_page(page, limit=MAX_TEXT):
    """Rendered text: main landmark first, then whole body, then a shadow-DOM-piercing walk."""
    try:
        page.wait_for_load_state("load", timeout=3500)
    except Exception:
        pass
    text = ""
    for script in ("() => { const r = document.querySelector('main, article, [role=main]'); return r ? r.innerText : ''; }",
                   "() => document.body ? document.body.innerText : ''", WALK):
        text = re.sub(r"\n{3,}", "\n\n", page.evaluate(script)).strip()
        if len(text) >= 300:
            break
    return text[:limit]


def read(session, limit=MAX_TEXT, vision=False):
    page = session.page
    page.wait_for_timeout(250)
    text = read_page(page, limit)
    shot = screenshot(session, {}).get("shot") if vision and len(text) < 80 and session.shot_dir else None
    links = page.evaluate("""() => [...document.querySelectorAll('a[href]')].filter(a => a.innerText.trim() && a.offsetParent)
        .slice(0, 25).map(a => ({text: a.innerText.trim().slice(0, 100), url: a.href}))""")
    return {"ok": len(text) >= 80, "title": page.title(), "url": page.url, "text": text, "links": links,
            "reason": None if len(text) >= 80 else "the page rendered almost no readable text",
            **({"shot": shot} if shot else {})}


def open_result(session, step):
    rows = session.last_results
    if not rows:
        raise StepError("No search results to open; search first")
    if "match" in step:
        needle = step["match"].lower()
        pick = next((r for r in rows if needle in (r["title"] + " " + r["url"]).lower()), None)
    else:
        index = int(step.get("index", 1))
        pick = rows[index - 1] if 1 <= index <= len(rows) else None
    if not pick:
        raise StepError("No result matches that request")
    result, tried = navigate(session, pick["url"]), []
    if not result["ok"] and "match" not in step:  # recover: bounded fall-through to the next ranked candidates
        for alt in [r for r in rows if r is not pick][:2]:
            tried.append({"url": pick["url"], "reason": result["reason"]})
            pick, result = alt, navigate(session, alt["url"])
            if result["ok"]:
                break
    return {**result, "chosen": pick, "recovered_from": tried}


def follow_link(session, step):
    page, before, count = session.page, session.page.url, len(session.tabs)
    link = page.get_by_role("link", name=re.compile(re.escape(step["text"]), re.I)).first
    if link.count() == 0:
        return {"ok": False, "reason": "no visible link named '%s'" % step["text"], "url": before}
    href = link.get_attribute("href") or ""
    if re.match(r"(?:javascript|mailto|tel|data|blob):", href.strip(), re.I):
        return {"ok": False, "reason": "link is not an ordinary web link", "url": before}
    try:
        link.click(timeout=4000)
        page.wait_for_load_state("domcontentloaded", timeout=9000)
    except Exception as error:
        return {"ok": False, "reason": type(error).__name__, "url": page.url}
    if len(session.tabs) > count:
        session.current = len(session.tabs) - 1
    now = session.page
    changed = now.url != before or len(session.tabs) > count
    return {"ok": changed, "url": now.url, "title": now.title(), "reason": None if changed else "the page did not change"}


def scroll(session, step):
    page, mode = session.page, step.get("to", "down")
    before = page.evaluate("() => Math.round(window.scrollY)")
    if mode == "top":
        page.evaluate("() => window.scrollTo(0, 0)")
    elif mode == "bottom":
        page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    else:
        sign = -1 if mode == "up" else 1
        page.evaluate("(n) => window.scrollBy(0, n)", sign * int(step.get("amount", 600)))
    page.wait_for_timeout(150)
    after = page.evaluate("() => Math.round(window.scrollY)")
    atend = page.evaluate("() => Math.round(window.scrollY + innerHeight) >= document.documentElement.scrollHeight - 2")
    return {"ok": after != before or (mode == "down" and atend) or (mode in ("up", "top") and before == 0),
            "scroll_before": before, "scroll_after": after, "at_end": atend, "url": page.url}


def find(session, step):
    page, text = session.page, step["text"]
    hits = page.get_by_text(text, exact=False)
    count = hits.count()
    if not count:
        return {"ok": False, "reason": "text not found on the rendered page", "url": page.url}
    first = next((hits.nth(i) for i in range(min(count, 12)) if hits.nth(i).is_visible()), None)
    if first is None:
        return {"ok": False, "reason": "text exists but is not visible on the rendered page", "url": page.url}
    first.scroll_into_view_if_needed(timeout=2000)
    snippet = (first.evaluate("e => (e.closest('p,li,h1,h2,h3,td,div') || e).innerText") or "")[:300]
    return {"ok": True, "matches": count, "snippet": snippet, "url": page.url}


def new_tab(session, step):
    if len(session.tabs) >= MAX_TABS:
        raise StepError("Tab limit reached")
    page = session.context.new_page()  # the context 'page' hook may already have adopted it
    if page not in session.tabs:
        session.tabs.append(page)
    session.current = len(session.tabs) - 1
    return navigate(session, step["url"]) if step.get("url") else {"ok": True, "url": "about:blank"}


def switch_tab(session, step):
    index = int(step["index"])
    if not 1 <= index <= len(session.tabs):
        raise StepError("No such tab")
    session.current = index - 1
    return {"ok": True, "url": session.page.url, "title": session.page.title()}


def close_tab(session, step):
    if len(session.tabs) == 1:
        raise StepError("Refusing to close the only tab")
    session.tabs.pop(session.current).close()
    session.current = min(session.current, len(session.tabs) - 1)
    return {"ok": True, "url": session.page.url}


def research(session, step):
    """Search, then read up to N distinct results; recover from blocked/failed pages."""
    query, want = step["query"], int(step.get("pages", 3))
    engines = [step.get("engine", "bing")]  # DuckDuckGo-HTML answers headless browsers with 403; the caller falls back to provider candidates
    log, sources, tried = [], [], 0
    budget_end = time.monotonic() + float(step.get("budget", 30))  # no single site may use up the whole job
    if step.get("urls") or step.get("candidates"):  # candidates supplied by the caller's own search provider; read them in the real browser
        engines = ["provided"]
    for engine in engines:
        if engine == "provided":
            given = step.get("candidates") or [{"url": u} for u in step["urls"]]
            rows = [{"rank": i + 1, "title": str(c.get("title", ""))[:200], "url": c["url"], "snippet": str(c.get("snippet", ""))[:300]}
                    for i, c in enumerate(given[:10]) if isinstance(c, dict) and str(c.get("url", "")).startswith(("https://", FIXTURE or "https://"))]
            if step.get("candidates"):  # rank by relevance BEFORE loading pages: the first few pages decide the job
                rows = (relevant(rows, query) or rows) if len(content_words(query)) >= 3 else rows
                words = [w for w in re.findall(r"[a-z0-9]{3,}", query.lower())]
                rows.sort(key=lambda r: -score(r, words))
            found = {"ok": True, "results": rows}
        else:
            found = search(session, engine, query)
        log.append({"engine": engine, "ok": found["ok"], "reason": found.get("reason")})
        if not found["ok"]:
            continue
        for row in found["results"]:
            if len(sources) >= want or tried >= want + 4:
                break
            if time.monotonic() > budget_end:
                log.append({"stopped": "research time budget used", "have": len(sources)})
                break
            if sum(urlsplit(s["url"]).hostname == urlsplit(row["url"]).hostname for s in sources) >= 2:
                continue  # at most two pages per site keeps the sources independent
            tried += 1
            nav = navigate(session, row["url"], timeout_ms=int(max(3000, min(12000, (budget_end - time.monotonic()) * 1000))))
            if not nav["ok"]:
                log.append({"skipped": row["url"], "reason": nav["reason"]})
                continue
            page = read(session, 6000)
            if not page["ok"]:
                log.append({"skipped": row["url"], "reason": page["reason"]})
                continue
            # body text only: a keyword-stuffed title must not vouch for unrelated content
            if len(content_words(query)) >= 3 and len(content_words(query) & content_words(page["text"])) < 2:
                log.append({"skipped": row["url"], "reason": "page is not about the query"})
                continue
            sources.append({"title": page["title"] or row["title"], "url": page["url"], "text": page["text"],
                            "found_via": engine, "rank": row["rank"]})
        if sources:
            break
    return {"ok": bool(sources), "sources": sources, "log": log,
            "reason": None if sources else "no readable public page could be reached"}


SENSITIVE_FIELD = re.compile(r"pass(?:word|wd)?|card|cvv|cvc|ssn|social|secret|token|otp|2fa|iban|routing|account|login|e-?mail|phone|address|birth", re.I)


def fill(session, step):
    """Type into one ordinary public text field and read the value back.  Never personal/credential fields."""
    page, text = session.page, step["text"]
    if len(text) > 300:
        raise StepError("Field text is limited to 300 characters")
    if "label" in step:
        field = page.get_by_label(re.compile(re.escape(step["label"]), re.I)).first
    elif "placeholder" in step:
        field = page.get_by_placeholder(re.compile(re.escape(step["placeholder"]), re.I)).first
    elif "name" in step:
        field = page.locator('input[name="%s"], textarea[name="%s"]' % (step["name"], step["name"])).first
    else:
        field = page.locator('input[type=search], input[type=text], input:not([type]), textarea').first
    if field.count() == 0:
        return {"ok": False, "reason": "no such field on the page", "url": page.url}
    attrs = field.evaluate("e => ({type: (e.type || 'text').toLowerCase(), name: e.name || '', id: e.id || '', ph: e.placeholder || '', "
                           "ac: e.autocomplete || '', label: (e.labels && e.labels[0] ? e.labels[0].innerText : '')})")
    if attrs["type"] not in ("text", "search", "textarea", "url", "number") or \
            SENSITIVE_FIELD.search(" ".join((attrs["name"], attrs["id"], attrs["ph"], attrs["ac"], attrs["label"]))):
        return {"ok": False, "reason": "refusing a credential or personal-data field", "url": page.url}
    field.fill(text, timeout=3000)
    back = field.input_value()
    return {"ok": back == text, "field": attrs["name"] or attrs["id"] or attrs["ph"], "value_matches": back == text, "url": page.url,
            "reason": None if back == text else "the field did not keep the typed value"}


def submit(session, step):
    """Press Enter in the focused field, only when its form is a read-only GET form; verify the page moved."""
    page, before = session.page, session.page.url
    method = page.evaluate("() => { const f = document.activeElement && document.activeElement.form; return f ? (f.method || 'get').toLowerCase() : null; }")
    if method != "get":
        return {"ok": False, "reason": "no focused GET form to submit" if method is None else "refusing to submit a %s form" % method, "url": before}
    page.keyboard.press("Enter")
    try:
        page.wait_for_url(lambda url: url != before, timeout=9000)
        page.wait_for_load_state("domcontentloaded", timeout=9000)
    except Exception:
        return {"ok": False, "reason": "the page did not change after the search form was submitted", "url": page.url}
    return {"ok": True, "url": page.url, "title": page.title()}


def screenshot(session, step):
    """Small JPEG for the vision fallback; written to the caller's private directory, never echoed on stdout."""
    shot_dir = session.shot_dir
    if not shot_dir:
        raise StepError("Screenshots are not enabled for this job")
    path = Path(shot_dir) / ("shot-%d.jpg" % int(time.time() * 1000))
    session.page.screenshot(path=str(path), type="jpeg", quality=50, full_page=False)
    return {"ok": True, "shot": str(path), "url": session.page.url, "title": session.page.title()}


PLAYBACK = """() => {
    const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
    const player = document.querySelector('.html5-video-player');
    if (!v) return null;
    return {t: v.currentTime, paused: v.paused, ended: v.ended, ready: v.readyState, dur: v.duration || 0,
            ad: !!(player && player.classList.contains('ad-showing')), muted: v.muted};
}"""


def playback(session, step):
    """Is the page's video actually advancing?  Muted autoplay only; distinguishes loaded page from playing video."""
    page, seconds = session.page, min(float(step.get("seconds", 3)), 8)
    try:
        page.wait_for_selector("video", state="attached", timeout=8000)
    except Exception:
        return {"ok": False, "state": "no-video", "reason": "no video element on the page", "url": page.url}
    first = page.evaluate(PLAYBACK)
    if first and (first["paused"] or first["ready"] < 2):
        page.evaluate("() => { const v = document.querySelector('video.html5-main-video') || document.querySelector('video'); v.muted = true; v.play().catch(() => 0); }")
    page.wait_for_timeout(int(seconds * 1000))
    last = page.evaluate(PLAYBACK)
    if last and last["ad"] and step.get("skip_ads"):  # YouTube's own Skip button, only when the page enables it
        button = page.locator(".ytp-skip-ad-button, .ytp-ad-skip-button-modern").first
        try:
            button.click(timeout=6500)
            page.wait_for_timeout(int(seconds * 1000))
            first, last = last, page.evaluate(PLAYBACK) or last
        except Exception:
            pass
    if not last:
        return {"ok": False, "state": "no-video", "reason": "the video element disappeared", "url": page.url}
    advanced = last["t"] - first["t"]
    state = "ended" if last["ended"] else "ad" if last["ad"] else "playing" if (advanced >= seconds * 0.5 and not last["paused"]) \
        else "buffering" if last["ready"] < 3 and not last["paused"] else "paused"
    return {"ok": state == "playing", "state": state, "advanced_s": round(advanced, 2), "position_s": round(last["t"], 1),
            "duration_s": round(last["dur"], 1) if last["dur"] == last["dur"] and last["dur"] != float("inf") else None,
            "url": page.url, "title": page.title(),
            "reason": None if state == "playing" else ("an advertisement is playing" if state == "ad" else "video is " + state)}


OPS = {"fill": fill, "submit": submit, "screenshot": screenshot, "playback": playback,
       "search": lambda s, st: search(s, st["engine"], st["query"]), "open_result": open_result, "goto": lambda s, st: navigate(s, st["url"]),
       "follow_link": follow_link, "scroll": scroll, "find": find, "read": lambda s, st: read(s, vision=bool(st.get("vision"))), "new_tab": new_tab,
       "switch_tab": switch_tab, "close_tab": close_tab, "research": research,
       "back": lambda s, st: (s.page.go_back(timeout=9000), {"ok": True, "url": s.page.url})[1]}


def execute(job, *, fixture=None):
    from playwright.sync_api import sync_playwright
    import os
    import tempfile
    global FIXTURE
    FIXTURE = fixture
    steps = job["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("Step budget")
    for step in steps:
        if step.get("op") not in OPS:
            raise ValueError("Unsupported browser step")
    policy = Egress(fixture, None, max_requests=260, max_bytes=60 * 1024 * 1024)
    proxy = start(policy)
    counts = {"requests": 0, "navigations": 0, "denied": 0}
    wants_media = any(step.get("op") == "playback" for step in steps)  # video needs its media; reading does not
    results, started = [], time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-browse-") as folder, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, chromium_sandbox=True, downloads_path=folder,
                proxy={"server": "http://127.0.0.1:" + str(proxy.server_address[1]), "bypass": "<-loopback>",
                       "username": policy.username, "password": policy.password},
                args=["--disable-quic", "--disable-gpu", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "--disable-background-networking",
                      "--disable-extensions", "--mute-audio"])
            context = browser.new_context(accept_downloads=False, service_workers="block", permissions=[],
                                          viewport={"width": 1280, "height": 800}, locale="en-US",
                                          user_agent=None)
            context.set_default_timeout(8000)
            context.route_web_socket("**/*", lambda ws: ws.on_message(lambda message: None))
            fixture_cfg = job.get("_fixture_cfg") if fixture else None

            def route(request_route):
                request = request_route.request
                counts["requests"] += 1
                p = urlsplit(request.url)
                local = fixture and p.scheme + "://" + p.netloc == fixture
                try:
                    if not (p.scheme == "https" or local) or counts["requests"] > 1500:
                        raise ValueError("budget")
                    if request.method not in ("GET", "HEAD"):
                        raise ValueError("write")
                    if request.is_navigation_request():
                        counts["navigations"] += 1
                        if counts["navigations"] > 40:
                            raise ValueError("navigation quota")
                    if not wants_media and TRACKERS.search(request.url):
                        raise ValueError("tracker")  # reading never needs ads or analytics
                    if request.resource_type in (("font",) if wants_media else ("media", "font", "image")):
                        raise ValueError("heavy")
                    request_route.continue_()
                except ValueError:
                    counts["denied"] += 1
                    request_route.abort()
            context.route("**/*", route)
            first = context.new_page()
            session = Session(context, fixture_cfg, first, job.get("shot_dir"))

            def adopt(popup):
                if len(session.tabs) < MAX_TABS and popup not in session.tabs:
                    session.tabs.append(popup)
                else:
                    popup.close()
            context.on("page", adopt)
            for page in (first,):
                page.on("dialog", lambda dialog: dialog.dismiss())
            context.on("dialog", lambda dialog: dialog.dismiss())
            context.on("download", lambda download: download.cancel())
            for step in steps:
                t0 = time.monotonic()
                try:
                    outcome = OPS[step["op"]](session, step)
                except StepError as error:
                    outcome = {"ok": False, "reason": str(error)}
                except Exception as error:
                    outcome = {"ok": False, "reason": type(error).__name__ + ": " + str(error).splitlines()[0][:200]}
                outcome["op"] = step["op"]
                outcome["ms"] = round((time.monotonic() - t0) * 1000)
                results.append(outcome)
                if not outcome["ok"] and not step.get("optional"):
                    break
                if time.monotonic() - started > 70:
                    results.append({"op": "budget", "ok": False, "reason": "time budget exhausted", "ms": 0})
                    break
            tabs = [{"url": p.url, "title": p.title()} for p in session.tabs]
            context.close()
            browser.close()
    finally:
        proxy.shutdown()
        proxy.server_close()
    return {"steps": results, "tabs": tabs, "limits": counts,
            "egress": {"bytes": policy.bytes, "connections": policy.requests, "denied": policy.blocked},
            "total_ms": round((time.monotonic() - started) * 1000),
            "isolation": "Ephemeral Chromium context; sandbox on; pinned public-HTTPS proxy; no profile, cookies, forms or downloads"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture-origin")
    ap.add_argument("--fixture-engine")
    args = ap.parse_args()
    raw = sys.stdin.read(16001)
    if len(raw) > 16000:
        raise ValueError("Request quota")
    job = json.loads(raw)
    if args.fixture_origin:  # test-only; never reachable from tool/HTTP schemas
        job["_fixture_cfg"] = {"engine_base": args.fixture_engine}
    print(json.dumps(execute(job, fixture=args.fixture_origin)))
