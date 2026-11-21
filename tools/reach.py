"""Internet reach (Agent Reach's no-login sources): free web search and page reading through Exa,
YouTube video info and subtitles through yt-dlp, and RSS/Atom feeds. No accounts are used.

Exa is called through mcporter (installed by `agent-reach install`); yt-dlp is installed with pipx.
"""
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from tools.registry import tool

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HOME = Path.home()
_PATHS = [Path(os.environ.get("APPDATA", "")) / "npm", Path(r"C:\Program Files\nodejs"), _HOME / ".local" / "bin"]
MAX_CHARS = 12000  # what goes back to the model per call


def _env() -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(p) for p in _PATHS] + [env.get("PATH", "")])
    return env


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=_env(), creationflags=_NO_WINDOW)


def _exe(name: str) -> str:
    """mcporter / yt-dlp: the Windows install locations, else whatever is on PATH (Linux server)."""
    import shutil

    for p in _PATHS:
        for cand in (p / f"{name}.cmd", p / f"{name}.exe", p / name):
            if cand.exists():
                return str(cand)
    return shutil.which(name, path=_env()["PATH"]) or name


def _exa(tool_name: str, args: dict, timeout: int = 60) -> str:
    exe = _exe("mcporter")
    r = _run([exe, "call", f"exa.{tool_name}", "--args", json.dumps(args), "--output", "text"], timeout)
    out = (r.stdout or "").strip()
    if r.returncode != 0 and not out:
        raise RuntimeError((r.stderr or "mcporter failed").strip()[-300:])
    return out


def _http_url(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


@tool(
    "Search the web for free (Exa): current info, news, facts, products, how-tos. Prefer this over web_search "
    "for everyday searches (web_search costs money each time). Describe the ideal page, not just keywords.",
    {"query": {"type": "string", "description": "e.g. 'official Apex Legends patch notes for the newest season'"},
     "objective": {"type": "string", "description": "What you need from the results, e.g. 'the release date and price'"},
     "results": {"type": "integer", "description": "How many results, 1-10 (default 5)"}},
    required=["query"],
)
def search_web_free(query: str, objective: str = "", results: int = 5) -> str:
    """Exa first, then the other free providers (core/websearch.py), with the results saved locally and returned
    as numbered sources with site, date and URL."""
    from core import users, websearch
    try:
        pack = websearch.search(query, max(1, min(int(results or 5), 8)), objective)
    except websearch.SearchUnavailable as e:
        return websearch.unavailable_text(query, e)
    except InterruptedError:
        return websearch.paused_text(query)
    except ValueError as e:
        return f"I can't search for that: {e}"
    websearch.remember(users.current(), "search", pack)
    return websearch.evidence_text(pack)[:MAX_CHARS]


def shop_search(query: str) -> list:
    """Find real product pages to BUY an item: web-search the described fit and return up to 6
    distinct stores as [{store, title, url}]. Used by the phone avatar tab's 'find this fit to buy'."""
    import re
    try:
        out = search_web_free(query + " buy online", objective="product pages to purchase", results=8)
    except Exception:
        return []
    skip = ("google.", "bing.", "pinterest.", "youtube.", "facebook.", "instagram.", "wikipedia.",
            "reddit.", "tiktok.", "lyst.", "etsy.com/search")
    seen, items = set(), []
    for u in re.findall(r"https?://[^\s\)\]\">]+", out):
        host = re.sub(r"https?://(www\.)?", "", u).split("/")[0].lower()
        if host in seen or any(b in host for b in skip) or len(items) >= 6:
            continue
        slug = u.rstrip("/").split("/")[-1].split("?")[0]
        title = re.sub(r"[-_]+", " ", re.sub(r"\.html?$", "", slug, flags=re.I)).strip()
        if len(re.sub(r"[^A-Za-z]", "", title)) < 4:   # mostly a product code → generic label
            title = "View product"
        seen.add(host)
        items.append({"store": host.replace(".com", "").replace(".co.uk", ""), "title": title[:64], "url": u})
    return items


@tool(
    "Read the full text of web pages (Exa), including pages that need JavaScript. Pass several links at once "
    "if you need them.",
    {"urls": {"type": "array", "items": {"type": "string"}}},
)
def read_web_pages(urls: list) -> str:
    urls = [u for u in (urls or []) if isinstance(u, str) and _http_url(u)][:5]
    if not urls:
        return "Give me http(s) links."
    from core import websearch
    parts = []
    for url in urls:
        try:
            page = websearch.open_page(url)      # direct pinned fetch, then the Exa readers; saved in the research store
            date = f"published {page['date']}" if page.get("date") else "date not stated"
            parts.append(f"## {page['title']} — {websearch._host(page['url'])} ({date}; read via {page['provider']})\n"
                         f"URL: {page['url']}\n{page['text'][:MAX_CHARS // len(urls)]}")
        except websearch.PageUnavailable as e:
            parts.append(f"## {url}\nI couldn't read anything from this page: " + "; ".join(f"{p} ({r})" for p, r in e.failures)
                         + ". It may be empty, blocked to automated readers or behind a login. Say so instead of guessing what it contains.")
        except (ValueError, OSError) as e:
            parts.append(f"## {url}\nI couldn't read anything from this page: {e}")
    return "\n\n".join(parts)[:MAX_CHARS]


def _vtt_to_text(vtt: str) -> str:
    """YouTube's auto-captions repeat each line several times; keep each spoken line once."""
    lines, seen = [], set()
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or line.isdigit():
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return " ".join(lines)


@tool(
    "Get a YouTube video's title, channel, length and full transcript (subtitles), to summarize it, answer "
    "questions about it, or pull out steps/recipes/tips. Requires accessible captions; report access and rate-limit failures without claiming a transcript was read.",
    {"url": {"type": "string"}, "language": {"type": "string", "description": "Subtitle language code, default 'en' (fr, ht...)"}},
    required=["url"], slow=True,
)
def youtube_transcript(url: str, language: str = "en") -> str:
    from core.tutorial_learning import video_url
    try:
        # Keep ordinary legacy HTTP links working by upgrading their transport.
        if isinstance(url,str) and url.startswith('http://'):url='https://'+url[7:]
        url=video_url(url)
    except (ValueError,TypeError):
        return "That isn't a YouTube link."
    lang = re.sub(r"[^a-zA-Z-]", "", language or "en") or "en"
    with tempfile.TemporaryDirectory() as tmp:
        r = _run([_exe("yt-dlp"), "--skip-download", "--no-simulate", "--write-subs", "--write-auto-subs",
                  "--sub-langs", f"{lang}.*,{lang}", "--sub-format", "vtt", "--no-playlist",
                  "--print", "%(title)s\t%(channel)s\t%(duration_string)s\t%(upload_date)s",
                  "-o", os.path.join(tmp, "%(id)s.%(ext)s"), url], timeout=120)
        info = (r.stdout or "").strip().splitlines()
        subs = sorted(Path(tmp).glob("*.vtt"), key=lambda p: (".orig" in p.name, len(p.name)))
        if not info:
            return ("Couldn't read that video: caption/video access is rate-limited (HTTP 429)." if '429' in (r.stderr or '')
                    else "Couldn't read that video. Authorized video/caption access was unavailable; no transcript was retrieved.")
        title, channel, length, date = (info[0].split("\t") + ["", "", "", ""])[:4]
        length = length if ":" in length else f"{length} seconds"
        head = f"{title} by {channel} ({length}, uploaded {date[:4]}-{date[4:6]}-{date[6:]})."
        if not subs:
            if '429' in (r.stderr or ''):
                return head+' Caption retrieval is rate-limited (HTTP 429). No transcript was retrieved; wait before retrying. This does not mean the video has no captions.'
            if r.returncode:
                return head+' Captions could not be retrieved. No transcript or video understanding is claimed.'
            return head + f" No {lang} subtitles are available for this video."
        text = _vtt_to_text(subs[0].read_text(encoding="utf-8", errors="replace"))
    return f"{head}\nTranscript:\n{text[:MAX_CHARS]}" + (" [cut short]" if len(text) > MAX_CHARS else "")


@tool(
    "Read the latest items from an RSS or Atom feed (news sites, blogs, podcasts, YouTube channel feeds).",
    {"url": {"type": "string"}, "count": {"type": "integer", "description": "How many items (default 8)"}},
    required=["url"],
)
def read_feed(url: str, count: int = 8) -> str:
    if not _http_url(url):
        return "Give me an http(s) feed link."
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis feed reader)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        root = ET.fromstring(resp.read(5_000_000))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for it in root.iter("item"):  # RSS
        items.append((it.findtext("title", ""), it.findtext("link", ""), it.findtext("pubDate", "")))
    for e in root.findall("a:entry", ns):  # Atom
        link = e.find("a:link", ns)
        items.append((e.findtext("a:title", "", ns), link.get("href", "") if link is not None else "",
                      e.findtext("a:updated", "", ns) or e.findtext("a:published", "", ns)))
    if not items:
        return "That doesn't look like an RSS/Atom feed."
    title = root.findtext("channel/title") or root.findtext("a:title", "", ns)
    lines = [f"{t.strip()} ({d.strip()[:25]}) {l.strip()}" for t, l, d in items[:max(1, min(int(count or 8), 30))]]
    return (f"{title}:\n" if title else "") + "\n".join(lines)
