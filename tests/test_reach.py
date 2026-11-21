"""Internet reach tools, offline parts (subtitle cleanup, feed parsing, input checks)."""
import io

from tools import reach

VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
<c>All right, so here we are</c>

00:00:02.000 --> 00:00:04.000
All right, so here we are
in front of the elephants
"""

RSS = b"""<?xml version="1.0"?><rss><channel><title>Pi News</title>
<item><title>New Pi</title><link>https://x/1</link><pubDate>Thu, 08 Oct 2026</pubDate></item>
<item><title>Old Pi</title><link>https://x/2</link><pubDate>Tue, 06 Oct 2026</pubDate></item></channel></rss>"""


def test_vtt_to_text_dedupes_and_strips_tags():
    assert reach._vtt_to_text(VTT) == "All right, so here we are in front of the elephants"


def test_read_feed(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(reach.urllib.request, "urlopen", lambda req, timeout=20: Resp(RSS))
    out = reach.read_feed("https://example.com/feed", 1)
    assert out.splitlines() == ["Pi News:", "New Pi (Thu, 08 Oct 2026) https://x/1"]


def test_rejects_bad_links():
    assert "YouTube" in reach.youtube_transcript("https://example.com/watch")
    assert "http" in reach.read_feed("file:///etc/passwd")
    assert "http" in reach.read_web_pages(["javascript:alert(1)"])


def test_search_passes_structured_args(monkeypatch):
    from core import websearch
    seen = {}
    monkeypatch.setattr(reach, "_exa", lambda name, args, timeout=60: seen.update(name=name, args=args)
                        or "Title: X\nURL: https://example.com/x\nPublished: 2026-01-02\nAuthor: N/A\nHighlights:\nabout pi 5 power\n")
    # (Exa is the first provider of the chain; the CLI presence check is bypassed so the suite never reaches the network)
    monkeypatch.setattr(websearch, "PROVIDERS", (("exa", lambda q, l, o="": websearch._parse_exa(
        reach._exa("web_search_exa", {"query": q, "objective": o or q, "numResults": l}))),))
    out = reach.search_web_free("pi 5 power supply", "", 50)
    assert out.startswith(websearch.MARK_SEARCH) and "1. X — example.com (published 2026-01-02)" in out
    assert "URL: https://example.com/x" in out
    assert seen == {"name": "web_search_exa", "args": {"query": "pi 5 power supply", "objective": "pi 5 power supply",
                                                       "numResults": 8}}
