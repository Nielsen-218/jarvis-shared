"""The shared design system: one stylesheet, linked by the pages that adopted it, with no
emoji icons and no native browser dialogs left behind on the converted pages."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
CSS = APP / "design-system.css"

# Pictographs and symbol glyphs that were used as icons (emoji, dingbats, arrows, shapes).
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿️]")
NATIVE_DIALOG = re.compile(r"(?<![\w.$])(?:window\.)?(confirm|prompt|alert)\s*\(")

LINKED = {  # page -> the href it must use (the desktop panel is a local file next to app/)
    APP / "index.html": "/design-system.css",
    APP / "dashboard.html": "/design-system.css",
    APP / "memory.html": "/design-system.css",
    APP / "wardrobe.html": "/design-system.css",
    APP / "wardrobe-3d.html": "/design-system.css",
    ROOT / "hud" / "index.html": "../app/design-system.css",
}
NO_NATIVE_DIALOGS = [APP / "index.html", APP / "memory.html", APP / "wardrobe.html", APP / "wardrobe-3d.html"]
NO_EMOJI = [APP / "index.html", ROOT / "hud" / "index.html", APP / "wardrobe.html"]


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    return TestClient(appserver.AppServer(lambda uid: None).app)


def read(p):
    return p.read_text(encoding="utf-8")


def test_tokens_match_the_research_and_the_companion_overlay():
    css = read(CSS)
    for name, value in {"--bg-0": "#06080C", "--bg-2": "#121823", "--text-1": "#E7EEF7", "--text-2": "#A3B1C4",
                        "--accent": "#5CC6E6", "--accent-ink": "#04161D", "--ok": "#6DD5A0", "--warn": "#E9B350",
                        "--danger": "#F07A84", "--r-2": "10px", "--r-3": "16px", "--s-4": "16px",
                        "--dur-base": "240ms", "--ease-out": "cubic-bezier(0.2, 0.8, 0.2, 1)"}.items():
        assert re.search(re.escape(name) + r":\s*" + re.escape(value) + ";", css), name


def test_stylesheet_has_every_component_and_the_accessibility_basics():
    css = read(CSS)
    for cls in ("ds-btn", "ds-iconbtn", "ds-card", "ds-chip", "ds-tabs", "ds-tab", "ds-input", "ds-list", "ds-row",
                "ds-sheet", "ds-scrim", "ds-toast", "ds-empty", "ds-skeleton", "ds-icon"):
        assert re.search(r"\." + cls + r"\b", css), cls
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "url(" not in css and "@import" not in css        # no remote assets: the app works offline


def test_stylesheet_is_served_and_cached_for_offline(client):
    r = client.get("/design-system.css")
    assert r.status_code == 200 and "--accent" in r.text
    assert "text/css" in r.headers["content-type"]
    sw = read(APP / "sw.js")
    assert '"/design-system.css"' in sw
    assert "jarvis-v31-missions" not in sw                    # the cache name was bumped with the new shell


@pytest.mark.parametrize("page", list(LINKED), ids=lambda p: p.parent.name + "/" + p.name)
def test_page_links_the_stylesheet(page):
    assert f'<link rel="stylesheet" href="{LINKED[page]}">' in read(page)


def test_phone_home_has_no_private_palette_left():
    html = read(APP / "index.html")
    style = html.split("</style>")[0]
    for old in ("--cyan", "--violet", "--panel", "--muted", "--grad", "#4fc3f7", "rgba(79,195,247"):
        assert old not in html, old
    assert not re.search(r"^\s*:root\s*\{", style, re.M)      # tokens come from the shared stylesheet only


@pytest.mark.parametrize("page", NO_NATIVE_DIALOGS, ids=lambda p: p.name)
def test_no_native_dialogs(page):
    html = read(page)
    hits = [m.group(0) for m in NATIVE_DIALOG.finditer(html)]
    assert not hits, hits
    assert "function dsDialog(" in html and "ds-sheet" in html


@pytest.mark.parametrize("page", NO_EMOJI, ids=lambda p: p.parent.name + "/" + p.name)
def test_no_emoji_icons(page):
    bad = [(i, m.group(0).encode("unicode_escape").decode()) for i, line in enumerate(read(page).splitlines(), 1)
           for m in EMOJI.finditer(line)]
    assert not bad, bad[:20]


def test_destructive_confirm_texts_are_unchanged():
    assert 'title: "Unlink this phone from Jarvis?"' in read(APP / "index.html")
    memory = read(APP / "memory.html")
    assert "Delete this memory and its recording for good?" in memory
    assert "Forget everything from the last 24 hours?" in memory
    classic = read(APP / "wardrobe-3d.html")          # the 3D room (classic), moved from wardrobe.html on 2026-11-12
    assert "from your closet?" in classic and "Rename this look" in classic and "Delete the look" in classic
    wardrobe = read(APP / "wardrobe.html")
    assert "from the closet?" in wardrobe and "Rename this look" in wardrobe and "Delete the look" in wardrobe
    assert "Delete this try-on?" in wardrobe


def test_wardrobe_page_is_the_person_first_and_keeps_the_3d_room_reachable():
    wardrobe = read(APP / "wardrobe.html")
    assert 'href="/wardrobe-3d.html"' in wardrobe and "3D room (classic)" in wardrobe
    assert "dress3d.js" not in wardrobe                          # the mannequin is no longer the default
    assert "dress3d.js" in read(APP / "wardrobe-3d.html")
    for route in ("/api/closet/tryon", "/api/closet/jobs", "/api/closet/photo", "/api/closet/garment", "/api/closet/look",
                  "/api/closet/style", "/api/closet/plan", "/api/closet/fit/delete"):
        assert route in wardrobe, route
    assert 'capture="environment"' in wardrobe                   # the phone camera adds a garment
    assert 'href="/wardrobe.html"' in read(APP / "wardrobe-3d.html")


def test_desktop_panel_draws_every_icon_it_names():
    """Each icon name the panel uses (games, results, pieces) exists in its inline icon set."""
    html = read(ROOT / "hud" / "index.html")
    block = html[html.index("const ICONS = {"):html.index("const ic = name")]
    known = set(re.findall(r"^\s+(\w+): '", block, re.M))
    used = set(re.findall(r"""\bic\(\s*["'](\w+)["']""", html))
    games = html[html.index("const GAMES = ["):html.index("];", html.index("const GAMES = ["))]
    used |= set(re.findall(r'^\s*\["\w+", "(\w+)",', games, re.M))
    assert len(known) > 30 and used and not (used - known), sorted(used - known)
    assert "pywebview" in html and 'id="b-eyes"' in html       # behaviour hooks untouched
