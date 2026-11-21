"""The kitchen, learning and PC pages run on the shared design system and keep every id, route and behaviour their
scripts rely on. These tests pin the kept-features list: a restyle that drops an id or a fetch route fails here."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
PAGES = {"kitchen": APP / "kitchen.html", "learning": APP / "learning.html", "pc": APP / "pc.html"}

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿️]")
NATIVE_DIALOG = re.compile(r"(?<![\w.$])(?:window\.)?(confirm|prompt|alert)\s*\(")

IDS = {
    "kitchen": ["today", "snap", "week", "pantry", "you", "toast", "main", "sheetRoot"],
    "learning": ["message", "teach", "title", "topic", "license", "content", "source", "file", "find", "query",
                 "lessons", "mentors", "audit"],
    "pc": ["connection", "refresh", "capture", "stop", "identity", "task", "open", "app", "website", "url", "play",
           "mute", "control", "command", "review", "review-note", "plan", "approve-plan", "budget", "usd", "workflow",
           "save-workflow", "workflow-name", "workflow-trigger", "usage", "usage-result", "result", "screen"],
}
# ids that kitchen.js builds (pantry panel, receipt scan, snap, shopping) and the data attributes its handlers read
KITCHEN_SCRIPT_IDS = ["rcpt", "cookBtn", "scanOut", "pantryList", "recipeOut", "addBtn", "pantryRetry", "shop",
                      "snapCam", "snapGal", "snapConfirm", "addMissing", "cookStart", "addToday"]
KITCHEN_ATTRS = ["data-tab", "data-open=", "data-rm=", "data-i=", "data-id=", "data-eat=", "data-vote=", "data-swap="]
ROUTES = {
    "kitchen": ["/api/kitchen", "/api/kitchen/v2", "/api/kitchen/pantry", "/api/kitchen/pantry/remove",
                "/api/kitchen/scan-receipt", "/api/kitchen/cook", "/api/kitchen/snap", "/api/kitchen/taste",
                "/api/kitchen/eat", "/api/kitchen/water", "/api/kitchen/weight", "/api/kitchen/swap",
                "/api/kitchen/replan", "/api/kitchen/household"],
    "learning": ["/api/teaching", "'/file'", "'/lesson'", "'/url'", "'/revoke'"],
    "pc": ["/api/pc/status", "/api/pc/policy/pending", "/api/pc/tasks", "/api/pc/screenshot", "/api/pc/cancel",
           "/api/pc/policy/approve", "/api/pc/workflows", "/api/pc/usage"],
}


def read(p):
    return p.read_text(encoding="utf-8")


def source(name):
    """The page plus the script it loads (pc.html keeps its logic in pc.js, kitchen.html in kitchen.js)."""
    html = read(PAGES[name])
    return html + {"pc": lambda: read(APP / "pc.js"), "kitchen": lambda: read(APP / "kitchen.js")}.get(name, lambda: "")()


@pytest.mark.parametrize("name", PAGES)
def test_page_uses_the_shared_design_system(name):
    html = read(PAGES[name])
    assert '<link rel="stylesheet" href="/design-system.css">' in html
    assert '<body class="ds">' in html
    style = html.split("</style>")[0]
    assert not re.search(r"^\s*:root\s*\{", style, re.M), "tokens come from design-system.css only"
    for old in ("--cyan", "--panel", "--muted", "#4fc3f7", "#0b1622", "#050b12", "rgba(79,195,247"):
        assert old not in html, old
    # colours inside the page's own CSS are tokens, not hex literals
    css = html.split("<style>")[1].split("</style>")[0]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), re.findall(r"#[0-9a-fA-F]{3,8}\b", css)


@pytest.mark.parametrize("name", PAGES)
def test_page_header_matches_the_other_pages(name):
    html = read(PAGES[name])
    assert 'class="ds-iconbtn ghost" href="/" aria-label="Back to Jarvis"' in html   # same back affordance as study/languages
    assert len(re.findall(r"<h1>", html)) == 1
    assert 'name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"' in html
    assert 'lang="en"' in html


@pytest.mark.parametrize("name", PAGES)
def test_page_keeps_its_ids(name):
    html = read(PAGES[name])
    for i in IDS[name]:
        assert f'id="{i}"' in html, i


@pytest.mark.parametrize("name", PAGES)
def test_page_keeps_its_routes(name):
    src = source(name)
    for route in ROUTES[name]:
        assert route in src, route


@pytest.mark.parametrize("name", PAGES)
def test_no_emoji_or_native_dialogs(name):
    src = source(name)
    assert not [m.group(0) for line in src.splitlines() for m in EMOJI.finditer(line)]
    assert not [m.group(0) for m in NATIVE_DIALOG.finditer(src)]


@pytest.mark.parametrize("name", PAGES)
def test_icon_only_controls_have_accessible_names(name):
    src = source(name)
    for tag in re.findall(r"<(?:a|button)\b[^>]*class=\"[^\"]*ds-iconbtn[^\"]*\"[^>]*>", src):
        assert "aria-label=" in tag, tag


def test_kitchen_keeps_tabs_pantry_and_shopping_behaviour():
    html, src = read(PAGES["kitchen"]), source("kitchen")
    assert '<script src="/kitchen.js"></script>' in html and '<link rel="stylesheet" href="/kitchen.css">' in html
    for tab in ("today", "snap", "week", "pantry", "you"):
        assert f'role="tab" aria-selected="{"true" if tab == "today" else "false"}" aria-controls="{tab}" data-tab="{tab}"' in html
    assert 'role="tablist"' in html
    for i in KITCHEN_SCRIPT_IDS:
        assert f'id="{i}"' in src or f'"{i}"' in src, i
    for a in KITCHEN_ATTRS:
        assert a in src, a
    assert "jarvis_kitchen_got_" in src                     # ticked shopping items persist per week
    assert 'capture="environment"' in src and 'accept="image/*"' in src       # the phone camera: snap and receipt
    assert 'jarvis_token' in src and 'location.hash.startsWith("#t=")' in src
    assert "if (r.status === 401) { location.href = \"/\"; return; }" in src       # an unlinked phone goes home
    assert "function totalQty(" in src and "toddler_rules" in src and "route.link" in src


def test_kitchen_demo_only_on_demo_flag():
    """The demo data is read only with ?demo=1, and demo mode never writes to the server."""
    src = read(APP / "kitchen.js")
    assert 'new URLSearchParams(location.search).get("demo") === "1"' in src
    assert 'async function write(path, body) { if (DEMO) return null; return apiPost(path, body); }' in src
    assert src.count("fetch(\"/kitchen-demo.json\"") == 1 and 'if (DEMO) { const r = await fetch("/kitchen-demo.json"' in src


def test_kitchen_demo_data_matches_the_contract():
    """app/kitchen-demo.json has the GET /api/kitchen/v2 shape (docs/KITCHEN-V2.md section 3) and is self-consistent."""
    import json
    j = json.loads(read(APP / "kitchen-demo.json"))
    for key in ("me", "household", "today", "week", "dishes", "cook_now", "pantry", "shopping", "snaps"):
        assert key in j, key
    assert {"kcal", "protein_g", "carbs_g", "fat_g", "water_l"} <= set(j["me"]["targets"]) and j["me"]["day_type"] in ("heavy", "run", "rest")
    ids = set(j["dishes"])
    assert len(ids) >= 12
    for i, d in j["dishes"].items():
        assert d["id"] == i and d["photo"] is None and d["servings"] == 5 and len(d["steps"]) <= 8
        m = d["macros"]
        assert abs(m["kcal"] - (4 * m["protein_g"] + 4 * m["carbs_g"] + 9 * m["fat_g"])) <= 0.1 * m["kcal"], i
        assert set(d["pairs_with"]) <= ids and d["taste"] in ("like", "dislike", None)
    for w in j["week"]:
        assert w["day_type"] in ("heavy", "run", "rest") and set(w["meals"].values()) <= ids
    assert {m["dish"] for m in j["today"]["meals"]} <= ids and {c["dish"] for c in j["cook_now"]} <= ids


def test_kitchen_states_are_honest():
    src = source("kitchen")
    assert "ds-skeleton" in src and "ds-spinner" in src              # loading
    for text in ("The kitchen did not load", "The pantry did not load", "Try again", "Link this phone first",
                 "No meal plan yet", "Nothing to buy yet", "The pantry is empty", "Let's set your targets",
                 "No meals planned for today", "Could not read that photo"):
        assert text in src, text
    assert "Not removed: " in src and "Not added: " in src and "Not logged: " in src   # a failed write says so
    assert 'aria-label="Remove ${esc(p.name)} from the pantry"' in src
    assert "(demo: not saved)" in src                                # demo writes say they were not saved


def test_learning_keeps_forms_and_limits():
    html = read(PAGES["learning"])
    assert 'accept=".pdf,.txt,.md,.py,.cpp,.cc,.c,.h,.hpp,.cs,.js,.ts,.java,.rs,.go,.lua,.json"' in html
    assert "5*1024*1024" in html and "File exceeds 5 MiB" in html
    assert 'role="status" aria-live="polite"' in html                # the message region
    for field in ("title", "topic", "license"):
        assert re.search(rf'<label for="{field}">.*?id="{field}" required', html), field
    assert "location.hash.slice(1)" in html and "jarvis_token" in html
    assert "received_unindexed" in html and "has not been independently verified" in html
    for text in ("The library did not load", "No lessons yet", "No matching lessons", "Link this phone in the Jarvis app first."):
        assert text in html, text
    assert "setAttribute('aria-label','Revoke mentor '" in html


def test_pc_keeps_actions_and_safety_copy():
    html, js = read(PAGES["pc"]), read(APP / "pc.js")
    assert '<script src="/pc.js"></script>' in html
    assert 'role="status"' in html                                   # the connection badge
    for text in ("JARVIS cannot bypass the Windows lock screen", "nothing paid runs without your OK",
                 "Approve this exact plan and run", "Authorize one planning call"):
        assert text in html, text
    for fn in ('"open_app"', '"open_website"', '"control_computer"', '"media_control"', "play_pause", "sessionStorage",
               "jarvis_pc_job", "crypto.randomUUID()"):
        assert fn in js, fn
    assert 'max="1"' in html and 'min="0.000001"' in html            # the one-dollar ceiling on a cloud planning call
    assert "ds-badge ok" in js and "ds-badge danger" in js           # connection badge uses the shared badge states
    assert "Is the PC awake and JARVIS running?" in js               # an unavailable PC says what to do
    assert "Loading…" in js and "Getting your PC screen…" in js      # slow calls show a loading state


@pytest.fixture
def node_exe():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


@pytest.mark.parametrize("script", ["pc.js", "kitchen.js"])
def test_page_script_parses(node_exe, script):
    out = subprocess.run([node_exe, "--check", str(APP / script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


def test_kitchen_quantity_totals(node_exe, tmp_path):
    """totalQty sums the same unit across recipes ("3 cups" + "1 cup"), joins different units, and keeps free text."""
    html = read(APP / "kitchen.js")
    fn = re.search(r"function totalQty\(list\) \{.*?\n\}\n", html, re.S).group(0)
    script = tmp_path / "qty.mjs"
    script.write_text(fn + """
const cases = [[["3 cups", "1 cup"], "4 cups"], [["1/2 cup", "1/2 cup"], "1 cup"], [["2", "1"], "3"], [["1 1/2 lb", "1/2 lb"], "2 lb"],
  [["a pinch"], "a pinch"], [["2 cans"], "2 cans"], [["1 lb", "2 cups"], "1 lb + 2 cups"], [[], ""]];
let bad = 0;
for (const [input, want] of cases) { const got = totalQty(input); if (got !== want) { bad++; console.log("FAIL", JSON.stringify(input), got, want); } }
process.exit(bad ? 1 : 0);
""", encoding="utf-8")
    out = subprocess.run([node_exe, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
