"""Fashion: wardrobe memory, style profile, outfit log, and shopping lookbooks.

Everything is stored locally in data/fashion/:
  wardrobe.json   items you own (+ photos/ with one picture of each)
  profile.json    your style preferences and sizes
  outfits.json    what you wore and how it rated
  *.html          wardrobe gallery and lookbooks, opened in your browser
"""
import html
import json
import uuid
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core import storage
from core.words import plural
from tools.registry import tool


def fdir():
    """The current person's fashion folder (data/users/<id>/fashion)."""
    return storage.user_dir() / "fashion"

CATEGORIES = ["top", "bottom", "outerwear", "shoes", "accessory", "full-body"]
PROFILE_FIELDS = ["body", "style", "likes", "dislikes", "sizes", "budget", "notes"]

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
# Structured body measurements. Stored in profile.json under "stats"; the readable
# summary is mirrored into profile["body"] (used by the avatar/try-on prompts) and
# height/weight are mirrored into the fitness profile so training uses the real numbers.
BODY_FIELDS = ["height_cm", "weight_kg", "build", "chest_cm", "waist_cm", "hip_cm", "inseam_cm", "shoe"]
BUILDS = ["slim", "lean", "athletic", "average", "muscular", "broad", "curvy", "fuller"]


def _load(name: str, default):
    path = fdir() / name
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, OSError):
            return default          # a damaged file (half-synced, hand-edited) reads as empty, it never breaks the page
        if isinstance(default, (list, dict)) and not isinstance(value, type(default)):
            return default          # e.g. a garments.json that holds a bare string
        return value
    return default


def _keep_damaged(path: Path, value) -> None:
    """_load reads a damaged file as empty, so the next save would replace it with almost nothing. Before that happens,
    keep the bytes that are there (cut off mid-sync, hand-edited, wrong shape) beside it, so a closet can be recovered."""
    try:
        if not path.exists():
            return
        raw = path.read_bytes()
        try:
            old = json.loads(raw.decode("utf-8"))
            if not isinstance(value, (list, dict)) or isinstance(old, type(value)):
                return                                  # a healthy file of the same shape: an ordinary save
        except (ValueError, UnicodeError):
            pass
        if raw.strip():
            import hashlib
            keep = path.with_name(f"{path.name}.damaged-{hashlib.sha1(raw).hexdigest()[:8]}.bak")
            if not keep.exists():
                keep.write_bytes(raw)
    except OSError as e:
        print(f"[fashion] could not keep the damaged {path.name}: {type(e).__name__}: {e}")


def _save(name: str, value) -> None:
    fdir().mkdir(parents=True, exist_ok=True)
    _keep_damaged(fdir() / name, value)
    (fdir() / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


# -- wardrobe -------------------------------------------------------------------
@tool(
    "Save a clothing item the user owns to their wardrobe, with a photo from the camera. "
    "Look at the item first (live frame or look_through_camera) so the description is accurate.",
    {
        "name": {"type": "string", "description": "Short name, e.g. 'black fitted tee with tan trim'"},
        "category": {"type": "string", "enum": CATEGORIES},
        "colors": {"type": "string", "description": "Main colors, e.g. 'black, tan'"},
        "description": {"type": "string", "description": "Fit, material, style, what it pairs well with"},
        "take_photo": {"type": "boolean", "description": "true to save the current camera view as the item's photo"},
    },
)
def add_to_wardrobe(name: str, category: str, colors: str, description: str, take_photo: bool) -> str:
    item_id = uuid.uuid4().hex[:6]
    photo = None
    if take_photo:
        import cv2

        from tools.eyes import capture_frame

        frame = capture_frame(high_detail=True)
        if frame is not None:
            (fdir() / "photos").mkdir(parents=True, exist_ok=True)
            photo = f"photos/{item_id}.jpg"
            cv2.imwrite(str(fdir() / photo), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    wardrobe = _load("wardrobe.json", [])
    wardrobe.append({"id": item_id, "name": name, "category": category, "colors": colors,
                     "description": description, "photo": photo, "added": date.today().isoformat()})
    _save("wardrobe.json", wardrobe)
    return f"Saved '{name}' as item {item_id}" + (" with a photo." if photo else " (no photo).") + \
        f" Wardrobe now has {plural(len(wardrobe), 'item')}."


@tool(
    "List the clothes in the user's wardrobe (text only).",
    {"category": {"type": "string", "enum": CATEGORIES + ["all"]}},
)
def list_wardrobe(category: str) -> str:
    items = [i for i in _load("wardrobe.json", []) if category == "all" or i["category"] == category]
    if not items:
        return "No items saved yet." if category == "all" else f"No {category} items saved."
    return "\n".join(f"[{i['id']}] {i['category']}: {i['name']} ({i['colors']}) - {i['description']}"
                     for i in items)


@tool(
    "Remove an item from the wardrobe (e.g. it was given away or saved by mistake).",
    {"item_id": {"type": "string"}},
)
def remove_from_wardrobe(item_id: str) -> str:
    wardrobe = _load("wardrobe.json", [])
    keep = [i for i in wardrobe if i["id"] != item_id]
    if len(keep) == len(wardrobe):
        return f"No item with id {item_id}."
    removed = next(i for i in wardrobe if i["id"] == item_id)
    if removed.get("photo"):
        (fdir() / removed["photo"]).unlink(missing_ok=True)
    _save("wardrobe.json", keep)
    return f"Removed '{removed['name']}'."


@tool("Open a visual gallery of the user's whole wardrobe in their browser.")
def show_wardrobe() -> str:
    items = _load("wardrobe.json", [])
    if not items:
        return "The wardrobe is empty."
    cards = []
    for cat in CATEGORIES:
        group = [i for i in items if i["category"] == cat]
        if group:
            cards.append(f"<h2>{cat.title()}</h2><div class='grid'>")
            for i in group:
                img = f"<img src='{html.escape(i['photo'])}'>" if i.get("photo") else "<div class='noimg'>no photo</div>"
                cards.append(f"<div class='card'>{img}<h3>{html.escape(i['name'])}</h3>"
                             f"<p class='muted'>{html.escape(i['colors'])} · added {i['added']}</p>"
                             f"<p>{html.escape(i['description'])}</p></div>")
            cards.append("</div>")
    return _open_page("wardrobe.html", "Wardrobe", plural(len(items), "item"), "".join(cards))


# -- style profile ----------------------------------------------------------------
@tool("Get the user's saved style profile (body measurements, style, likes, dislikes, sizes, budget, notes).")
def get_style_profile() -> str:
    profile = _load("profile.json", {})
    if not profile:
        return "No style profile yet. Learn it as you go (ask about style, fits, colors, sizes, budget)."
    return "\n".join(f"{k}: {v}" for k, v in profile.items())


@tool(
    "Save something about the user's taste to their style profile. Replaces that field, so "
    "include anything already there that should be kept.",
    {
        "field": {"type": "string", "enum": PROFILE_FIELDS},
        "value": {"type": "string", "description": "e.g. style='streetwear, clean minimal', sizes='M tops, 32x32 jeans, 10.5 US shoes'"},
    },
)
def update_style_profile(field: str, value: str) -> str:
    profile = _load("profile.json", {})
    profile[field] = value
    _save("profile.json", profile)
    return f"Style profile updated: {field} = {value}"


# -- outfit log ---------------------------------------------------------------------
@tool(
    "Record an outfit the user is wearing (after an outfit check, or when they say what they wore).",
    {
        "description": {"type": "string", "description": "The outfit, e.g. 'black tee, grey sweats, sandals'"},
        "item_ids": {"type": "array", "items": {"type": "string"}, "description": "Wardrobe ids of the pieces, if known"},
        "rating": {"type": "integer", "description": "Your score out of 10, or 0 if not rated"},
        "occasion": {"type": "string", "description": "e.g. 'home', 'class', 'date', 'gym'"},
    },
)
def log_outfit(description: str, item_ids: list, rating: int, occasion: str) -> str:
    log = _load("outfits.json", [])
    log.append({"date": date.today().isoformat(), "time": datetime.now().strftime("%H:%M"),
                "description": description, "item_ids": item_ids, "rating": rating, "occasion": occasion})
    _save("outfits.json", log)
    return "Outfit logged."


@tool(
    "Get the outfits the user wore recently, to avoid repeats and learn what rates well.",
    {"days": {"type": "integer", "description": "How many days back to look"}},
)
def recent_outfits(days: int) -> str:
    since = (date.today() - timedelta(days=days)).isoformat()
    log = [o for o in _load("outfits.json", []) if o["date"] >= since]
    if not log:
        return f"No outfits logged in the last {plural(days, 'day')}."
    return "\n".join(f"{o['date']} ({o['occasion']}): {o['description']}"
                     + (f" - rated {o['rating']}/10" if o["rating"] else "") for o in log)


# -- shopping lookbook ---------------------------------------------------------------
@tool(
    "Open a lookbook / shopping page in the user's browser showing outfit or shopping "
    "recommendations (e.g. trending pieces to buy), with links to real product or store pages "
    "you found by web search. Use this whenever recommending things to buy, and when the user "
    "says 'order that outfit' or 'find me that look' — search the web for each piece and put a "
    "Buy link to the actual product. The user taps Buy and checks out on the retailer themselves; "
    "never ask for or enter card details.",
    {
        "title": {"type": "string", "description": "e.g. 'Trending tees for fall 2026'"},
        "intro": {"type": "string", "description": "One or two sentences on the trend / why these picks"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Product or piece name"},
                    "why": {"type": "string", "description": "Why it fits the trend and the user's style"},
                    "price": {"type": "string", "description": "e.g. '$35', or 'varies'"},
                    "url": {"type": "string", "description": "http(s) link to the product, store or search page"},
                    "pairs_with": {"type": "string", "description": "What to wear it with, ideally pieces the user owns"},
                },
                "required": ["name", "why", "price", "url", "pairs_with"],
                "additionalProperties": False,
            },
        },
    },
)
def create_lookbook(title: str, intro: str, items: list) -> str:
    cards = ["<div class='grid'>"]
    for it in items:
        url = it["url"] if urlparse(it["url"]).scheme in ("http", "https") else ""
        link = f"<a class='btn' href='{html.escape(url)}' target='_blank' rel='noopener'>Buy &rarr;</a>" if url else ""
        cards.append(f"<div class='card'><h3>{html.escape(it['name'])}</h3>"
                     f"<p class='price'>{html.escape(it['price'])}</p><p>{html.escape(it['why'])}</p>"
                     f"<p class='muted'>Pairs with: {html.escape(it['pairs_with'])}</p>{link}</div>")
    cards.append("</div>")
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40] or "lookbook"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return _open_page(f"lookbook-{stamp}-{slug}.html", title, intro, "".join(cards))


def _open_page(filename: str, title: str, subtitle: str, body: str) -> str:
    fdir().mkdir(parents=True, exist_ok=True)
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root {{ --bg:#0b0f14; --panel:#121923; --text:#e6edf3; --muted:#8b98a5; --accent:#4fc3f7; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:32px 16px; background:var(--bg); color:var(--text);
       font:15px/1.5 "Segoe UI",system-ui,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; }}
.tag {{ color:var(--accent); letter-spacing:.2em; font-size:12px; }}
h1 {{ margin:4px 0 6px; font-size:28px; }} h2 {{ margin:32px 0 12px; font-size:18px; color:var(--accent); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; }}
.card {{ background:var(--panel); border:1px solid #1f2a36; border-radius:12px; padding:14px; }}
.card img, .noimg {{ width:100%; aspect-ratio:4/3; object-fit:cover; border-radius:8px; background:#1a2330; }}
.noimg {{ display:grid; place-items:center; color:var(--muted); }}
h3 {{ margin:10px 0 4px; font-size:16px; }} p {{ margin:6px 0; }}
.muted {{ color:var(--muted); font-size:13px; }} .price {{ color:var(--accent); font-weight:600; }}
.btn {{ display:inline-block; margin-top:8px; padding:6px 12px; border-radius:8px; background:var(--accent);
       color:#00111c; text-decoration:none; font-weight:600; }}
</style></head><body><main>
<div class="tag">J.A.R.V.I.S. · FASHION</div><h1>{html.escape(title)}</h1>
<p class="muted">{html.escape(subtitle)}</p>{body}
</main></body></html>"""
    path = (fdir() / filename).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    webbrowser.open(path.as_uri())
    from core import hud

    hud.line("sys", f"Opened in your browser: {title}")
    return f"Opened '{title}' in the browser."


# -- outfit calendar -----------------------------------------------------------------
@tool(
    "Plan an outfit for a date (outfit calendar). Planning a date again replaces its outfit.",
    {
        "day": {"type": "string", "description": "ISO date, e.g. 2026-10-12"},
        "description": {"type": "string", "description": "The outfit, e.g. 'black tee with tan trim, dark jeans, white sneakers'"},
        "item_ids": {"type": "array", "items": {"type": "string"}, "description": "Wardrobe ids, if any"},
        "occasion": {"type": "string", "description": "e.g. 'work', 'church', 'date night'"},
    },
)
def plan_outfit(day: str, description: str, item_ids: list, occasion: str) -> str:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return "Use an ISO date like 2026-10-12."
    cal = _load("calendar.json", {})
    cal[d.isoformat()] = {"description": description, "item_ids": item_ids, "occasion": occasion}
    _save("calendar.json", cal)
    return f"Planned for {d.strftime('%A %d %B')}: {description}."


@tool(
    "Read the outfit calendar for the coming days (and optionally open it as a visual page).",
    {"days": {"type": "integer"}, "show_page": {"type": "boolean"}},
)
def outfit_calendar(days: int, show_page: bool) -> str:
    cal = _load("calendar.json", {})
    wardrobe = {i["id"]: i for i in _load("wardrobe.json", [])}
    start = date.today()
    rows, cards = [], ["<div class='grid'>"]
    for n in range(max(1, min(days, 31))):
        d = (start + timedelta(days=n)).isoformat()
        plan = cal.get(d)
        label = (start + timedelta(days=n)).strftime("%a %d %b")
        rows.append(f"{d} ({label}): " + (f"{plan['description']} [{plan['occasion']}]" if plan else "nothing planned"))
        if plan:
            pics = "".join(f"<img src='{html.escape(wardrobe[i]['photo'])}' style='width:48%;aspect-ratio:1'>"
                           for i in plan["item_ids"] if i in wardrobe and wardrobe[i].get("photo"))
            cards.append(f"<div class='card'><h3>{label}</h3><p class='muted'>{html.escape(plan['occasion'])}</p>"
                         f"<p>{html.escape(plan['description'])}</p>{pics}</div>")
    cards.append("</div>")
    if show_page:
        _open_page("calendar.html", "Outfit calendar", f"Next {plural(days, 'day')}", "".join(cards))
    return "\n".join(rows)


# -- phone/PC wardrobe tab -----------------------------------------------------------
def app_data() -> dict:
    """Everything the Wardrobe page shows (core.appserver serves it as the signed-in person)."""
    items = _load("wardrobe.json", [])
    for i in items:
        i["photo_url"] = f"fashion/{i['photo']}" if i.get("photo") else None
    ideas = _load("ideas.json", [])
    cal = _load("calendar.json", {})
    profile = _load("profile.json", {})
    return {"items": items, "categories": CATEGORIES, "profile": profile,
            "stats": profile.get("stats", {}), "body_fields": BODY_FIELDS, "builds": BUILDS,
            "avatar": {"photo_url": _avatar_url(False), "stylized_url": _avatar_url(True)},
            "tryon_url": _tryon_url(), "saved_outfits": named_outfits(),
            "look": profile.get("avatar_look", {}), "glb": profile.get("avatar_glb", ""),
            "outfits": _load("outfits.json", [])[-15:][::-1], "ideas": ideas[-20:][::-1],
            "planned": [{"date": d, **p} for d, p in sorted(cal.items()) if d >= date.today().isoformat()][:7]}


def generate_outfit(occasion: str = "", vibe: str = "") -> dict:
    """Ask the fast model to build an outfit from the clothes the person actually owns, and save the idea."""
    from core.intelligence import local_only
    if local_only():
        return {"error":"Automatic outfit generation requires cloud inference, which is disabled in local-only mode. Your wardrobe and saved outfits remain available."}
    import os

    import anthropic

    items = _load("wardrobe.json", [])
    if len(items) < 2:
        return {"error": "Add a few clothes to your wardrobe first (snap them with the camera or in chat)."}
    profile = _load("profile.json", {})
    recent = [o.get("description", "") for o in _load("outfits.json", [])[-8:]]
    closet = "\n".join(f"[{i['id']}] {i['category']}: {i['name']} ({i['colors']}) - {i['description']}" for i in items)
    prompt = (
        "You are a sharp personal stylist. Build ONE outfit using ONLY the items from this person's closet "
        "(use their ids). Mix a top/bottom or full-body, add outerwear/shoes/accessories when they fit.\n"
        f"Closet:\n{closet}\n"
        + (f"Style profile: {json.dumps(profile)}\n" if profile else "")
        + (f"Occasion: {occasion}\n" if occasion else "")
        + (f"Vibe wanted: {vibe}\n" if vibe else "")
        + (f"Avoid repeating these recent looks: {recent}\n" if recent else "")
        + 'Reply JSON only: {"name": short catchy name, "item_ids": [ids used], "why": one or two sentences on why '
          'it works and any styling tip (cuff, tuck, layer)}.')
    model = os.getenv("JARVIS_FAST_MODEL", "claude-haiku-5-5")
    r = anthropic.Anthropic().messages.create(model=model, max_tokens=1600,
                                              messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in r.content if b.type == "text").strip()
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        out = json.loads(raw)
    except ValueError:
        return {"error": "Couldn't put an outfit together just now; try again."}
    owned = {i["id"] for i in items}
    out["item_ids"] = [i for i in out.get("item_ids", []) if i in owned]
    idea = {"name": str(out.get("name", "Outfit"))[:80], "item_ids": out["item_ids"],
            "why": str(out.get("why", ""))[:400], "occasion": occasion,
            "created": datetime.now().isoformat(timespec="minutes")}
    ideas = _load("ideas.json", [])
    ideas.append(idea)
    _save("ideas.json", ideas[-100:])
    return idea


# -- avatar & body (per person; drives the try-on likeness and fitness) ---------------
def _avatar_file(stylized: bool):
    """Newest avatar image for the current person: the stylized render or the real photo."""
    d = fdir() / "avatar"
    if not d.exists():
        return None
    files = [f for f in d.iterdir() if f.suffix.lower() in IMAGE_EXT
             and f.name.startswith("stylized-") == stylized]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def _avatar_url(stylized: bool):
    f = _avatar_file(stylized)
    return f"fashion/avatar/{f.name}" if f else None


def save_avatar_photo(data: bytes) -> dict:
    """Save an uploaded face/body photo as the person's newest avatar photo (used for try-ons).

    Validates it is a real image via PIL, so a bad upload can't land in the folder.
    """
    from io import BytesIO

    from PIL import Image

    from PIL import ImageOps

    img = Image.open(BytesIO(data))
    img.load()
    img = ImageOps.exif_transpose(img).convert("RGB")   # phone photos carry their rotation in EXIF
    img.thumbnail((2048, 2048))  # plenty for likeness; keeps files sane
    adir = fdir() / "avatar"
    adir.mkdir(parents=True, exist_ok=True)
    path = adir / f"photo-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
    img.save(path, "JPEG", quality=92)
    return {"avatar_photo_url": f"fashion/avatar/{path.name}"}


def _body_sentence(s: dict) -> str:
    """A readable build description from the stats, for the avatar/try-on prompts."""
    bits = []
    if s.get("height_cm"):
        inches = round(float(s["height_cm"]) / 2.54)
        bits.append(f"{inches // 12}'{inches % 12}\"")
    if s.get("weight_kg"):
        bits.append(f"{round(float(s['weight_kg']) * 2.20462)} lb")
    if s.get("build"):
        bits.append(f"{s['build']} build")
    frame = [f"{label} {round(float(s[k]))} cm"
             for k, label in (("chest_cm", "chest"), ("waist_cm", "waist"), ("hip_cm", "hips"), ("inseam_cm", "inseam"))
             if s.get(k)]
    out = ", ".join(bits)
    if frame:
        out = (out + " · " if out else "") + ", ".join(frame)
    return out


def set_avatar_look(look: dict) -> dict:
    """Save the game-studio avatar look (sex, skin, hair, equipped preset items + colors) for this person."""
    profile = _load("profile.json", {})
    profile["avatar_look"] = look
    _save("profile.json", profile)
    return look


def _glb_path():
    """Where this person's cached 3D avatar (.glb) lives."""
    return fdir() / "avatar" / "avatar.glb"


def has_avatar_glb() -> bool:
    return _glb_path().exists()


def save_avatar_glb_bytes(data: bytes, source: str = "") -> str:
    """Store an already-validated .glb avatar uploaded straight from the phone (no internet
    needed). `source` is an optional note (e.g. 'avaturn') shown back in the app."""
    path = _glb_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    profile = _load("profile.json", {})
    profile["avatar_glb"] = source or "uploaded"
    _save("profile.json", profile)
    return profile["avatar_glb"]


def set_avatar_glb(url: str) -> str:
    """Cache this person's Ready Player Me (or any https .glb) 3D avatar locally.

    The model is downloaded once and then served from Jarvis itself, so the phone loads
    it same-origin: no CORS, it keeps working if the source CDN is slow or down, and it
    survives offline. An empty url clears the avatar. Raises ValueError on a bad link.
    """
    import urllib.request

    profile = _load("profile.json", {})
    url = (url or "").strip()
    path = _glb_path()
    if not url:
        profile.pop("avatar_glb", None)
        _save("profile.json", profile)
        path.unlink(missing_ok=True)
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis avatar)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(40 * 1024 * 1024 + 1)
    except Exception as error:  # network, DNS, HTTP error -> a clean message for the phone
        raise ValueError("Couldn't fetch that 3D avatar. Check the link is the GLB from Ready Player Me.") from error
    if len(data) > 40 * 1024 * 1024:
        raise ValueError("That 3D avatar file is too large (keep it under 40 MB).")
    # Binary glTF starts with the ASCII magic "glTF"; a 404 page or wrong link will not.
    if data[:4] != b"glTF":
        raise ValueError("That link didn't return a 3D avatar (.glb). Copy the GLB link from Ready Player Me.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    profile["avatar_glb"] = url
    _save("profile.json", profile)
    return url


def _tryon_url():
    """Newest realistic try-on result image for this person, if any."""
    d = fdir() / "tryons"
    if not d.exists():
        return None
    imgs = [f for f in d.iterdir() if f.suffix.lower() in IMAGE_EXT and not f.name.startswith("compare-")]
    if not imgs:
        return None
    return f"fashion/tryons/{max(imgs, key=lambda f: f.stat().st_mtime).name}"


def save_named_outfit(name: str, items: dict) -> dict:
    """Save the current Wardrobe Studio selection (garments + look) under a name, so it
    can be reopened in either the 3D or Photo mode."""
    outfits = [o for o in _load("saved_outfits.json", []) if o.get("name", "").lower() != name.strip().lower()]
    outfit = {"id": uuid.uuid4().hex[:6], "name": str(name).strip()[:60], "items": items,
              "created": datetime.now().isoformat(timespec="minutes")}
    outfits.append(outfit)
    _save("saved_outfits.json", outfits[-50:])
    return outfit


def named_outfits() -> list:
    return _load("saved_outfits.json", [])[-50:][::-1]


def set_body_stats(stats: dict) -> dict:
    """Save structured body measurements, update the styling build sentence, and keep
    height/weight in sync with the fitness profile. Returns the saved stats."""
    profile = _load("profile.json", {})
    cur = dict(profile.get("stats", {}))
    for k in BODY_FIELDS:
        v = stats.get(k)
        if v in (None, ""):
            continue
        if k.endswith("_cm") or k == "weight_kg":
            try:
                v = round(float(v), 1)
            except (TypeError, ValueError):
                continue
        else:
            v = str(v)[:40]
        cur[k] = v
    profile["stats"] = cur
    sentence = _body_sentence(cur)
    if sentence:
        profile["body"] = sentence
    _save("profile.json", profile)
    try:  # the AFT coach uses the real height/weight too
        from tools import fitness

        if cur.get("height_cm"):
            inches = round(float(cur["height_cm"]) / 2.54)
            fitness.set_fitness_profile("height", f"{inches // 12}'{inches % 12}\"")
        if cur.get("weight_kg"):
            fitness.set_fitness_profile("weight_lbs", str(round(float(cur["weight_kg"]) * 2.20462)))
    except Exception:
        pass
    return cur
