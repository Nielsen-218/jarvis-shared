"""The 3D dressing room's data, stylist and online discovery (UI: app/wardrobe.html + app/dress3d.js).

My Closet  = wardrobe.json (clothes the person owns), each with a wearable 3D model (tools/garment3d.py).
Discover   = garments.json (clothes found online: product image, store, link). Never mixed into My Closet.
Basics     = plain stand-ins the stylist may use when the closet has nothing for a slot (always labelled).
A look     = {slot: {"src": "closet"|"discover"|"basic", "id": ...}} for slots top/outer/bottom/shoes/hat/watch/neck/eyes.

Files that both machines may write are kept per machine and merged on read (no Syncthing conflicts):
current-<host>.json (the look on the avatar now) and looks-<host>.json (saved looks, with deletions).
"""
import colorsys
import json
import re
import threading
import time
import uuid
from datetime import datetime

from core import storage, users
from core.words import plural
from tools.registry import tool

SLOTS = ["top", "outer", "bottom", "shoes", "hat", "watch", "neck", "eyes"]
FORMALITY = {"tee": 1, "tank": .5, "longsleeve": 1.5, "hoodie": 1, "zip_hoodie": 1.1, "sweater": 2.5, "polo": 2.5, "shirt": 3.6,
             "jacket": 2.6, "vest": 2.2, "blazer": 4.6, "coat": 4, "jeans": 2, "joggers": .8, "shorts": .8, "leggings": .6, "trousers": 3.6,
             "skirt": 2.6, "dress": 3.2, "sneakers": 1.5, "hightops": 1.4, "boots": 2.6, "dress_shoes": 4.6, "sandals": .5,
             "cap": .5, "beanie": 1, "watch": 3, "chain": 2, "glasses": 2.2}
OCCASIONS = {   # target formality range, label
    "gym": (0, 1.1, "Gym"), "lounge": (0, 1.3, "Lounge"), "casual": (.9, 2.3, "Casual"), "streetwear": (1, 2.3, "Streetwear"),
    "date": (2.2, 3.6, "Date night"), "smart casual": (2.2, 3.5, "Smart casual"), "party": (2, 3.6, "Night out"),
    "work": (3, 4.4, "Work"), "church": (3.3, 5, "Church"), "formal": (3.8, 5, "Formal"), "interview": (3.6, 5, "Interview"),
    "wedding": (3.8, 5, "Wedding"), "luxury casual": (2, 3.4, "Luxury casual"),
}
OCC_WORDS = [("gym", r"\bgym|workout|training|run(ning)?\b"), ("lounge", r"lounge|chill at home|comfy|relax"),
             ("church", r"church|sunday service|mass\b"), ("wedding", r"wedding"), ("interview", r"interview"),
             ("formal", r"formal|classy|elegant|dressy|suit up|black tie|gala"), ("work", r"\bwork|office|business|meeting"),
             ("date", r"\bdate\b|dinner date|romantic"), ("party", r"party|night out|club|going out"),
             ("luxury casual", r"luxury|old money|quiet luxury|upscale"), ("streetwear", r"street\s?wear|street style|drip|hype"),
             ("smart casual", r"smart casual|brunch|smart"), ("casual", r"casual|everyday|chill|errands|relaxed")]
BASICS = {   # stand-ins when the closet has nothing for a slot
    "b-tee": {"name": "Plain white tee (basic)", "template": "tee", "colors": ["#f2f1ec"]},
    "b-shirt": {"name": "Crisp white shirt (basic)", "template": "shirt", "colors": ["#f4f4f1"]},
    "b-jeans": {"name": "Dark indigo jeans (basic)", "template": "jeans", "colors": ["#2b3a55"]},
    "b-trousers": {"name": "Charcoal trousers (basic)", "template": "trousers", "colors": ["#3a3c41"]},
    "b-sneakers": {"name": "White leather sneakers (basic)", "template": "sneakers", "colors": ["#f3f2ee"], "sole": "#f3f2ee"},
    "b-dress_shoes": {"name": "Black dress shoes (basic)", "template": "dress_shoes", "colors": ["#141416"], "sole": "#141416"},
    "b-blazer": {"name": "Navy blazer (basic)", "template": "blazer", "colors": ["#1f2a44"]},
    "b-jacket": {"name": "Black bomber jacket (basic)", "template": "jacket", "colors": ["#17171a"]},
    "b-chinos": {"name": "Beige chinos (basic)", "template": "trousers", "colors": ["#cbb68f"]},
}


def _fd():
    from tools import fashion
    return fashion.fdir()


def _host() -> str:
    from core import pcbridge
    return "pc" if pcbridge.local_pc() else "server"


def _load(name, default):
    from tools import fashion
    return fashion._load(name, default)


def _save(name, value):
    from tools import fashion
    fashion._save(name, value)


# -- the catalogue the page and the stylist work from -------------------------------------------------
def _model_for_closet(item: dict) -> dict:
    from tools import garment3d
    m = garment3d.meta(item["id"])
    if m and m.get("texture"):
        m = {**m, "texture": f"fashion/3d/{item['id']}/{m['texture']}"}
    return m or garment3d.basic_model(item)


def _wardrobe() -> list:
    """wardrobe.json (written by the PC) plus photos the phone server queued while the PC was away."""
    items = [i for i in _load("wardrobe.json", []) if isinstance(i, dict) and i.get("id")]
    have = {i["id"] for i in items}
    return items + [{**p, "pending": True} for p in _load("wardrobe-pending-server.json", [])
                    if isinstance(p, dict) and p.get("id") and p["id"] not in have]


def closet(jobs: dict | None = None) -> list:
    from tools import garment3d
    out = []
    for i in _wardrobe():
        if not isinstance(i, dict) or not i.get("id"):
            continue
        m = _model_for_closet(i)
        job = garment3d.job(i["id"]) or (jobs or {}).get(i["id"])
        if not job and i.get("pending"):
            job = {"state": "waiting", "detail": "Saved. It becomes 3D when your PC is on."}
        elif not job and i.get("photo") and m.get("status") != "ready" and not m.get("failed"):
            job = {"state": "waiting", "detail": "Waiting for the PC to make it 3D."}
        out.append({"src": "closet", "id": i["id"], "name": i.get("name", ""), "category": i.get("category", ""),
                    "colors_text": i.get("colors", ""), "description": i.get("description", ""), "occasions": i.get("occasions", []),
                    "favorite": bool(i.get("favorite")), "photo": f"fashion/{i['photo']}" if i.get("photo") else None,
                    "thumb": _clean_thumb(i, m),
                    "added": i.get("added", ""), "model": m, "job": job})
    return out


def _clean_thumb(item: dict, model: dict) -> str | None:
    """The clean product shot made from a worn photo, when there is one: the nicest closet thumbnail."""
    src = model.get("source") or ""
    if item.get("photo") and src.endswith("-clothes.jpg") and (_fd() / "photos" / src).exists():
        return f"fashion/photos/{src}"
    return None


# -- closet edits: wardrobe.json has one writer, the PC (the phone server relays, or queues adds) -------------
def _photo_bytes(data) -> bytes:
    import base64
    if isinstance(data, str):
        data = base64.b64decode(data.split(",", 1)[-1])
    if not data or len(data) > 20 * 1024 * 1024:
        raise ValueError("That photo is empty or too big.")
    from io import BytesIO

    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
    im.thumbnail((1800, 1800))
    out = BytesIO(); im.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _store_photo(item_id: str, data, suffix: str = "") -> str:
    rel = f"photos/{item_id}{suffix}.jpg"
    (_fd() / "photos").mkdir(parents=True, exist_ok=True)
    (_fd() / rel).write_bytes(_photo_bytes(data))
    return rel


def import_pending() -> int:
    """On the PC: adopt photos the phone server saved while the PC was off, and make them 3D."""
    from tools import garment3d
    pend = _load("wardrobe-pending-server.json", [])
    items = _load("wardrobe.json", [])
    have = {i["id"] for i in items}
    new = [{k: v for k, v in p.items() if k != "pending"} for p in pend if p["id"] not in have]
    if new:
        _save("wardrobe.json", items + new)
        for p in new:
            garment3d.process_async(p["id"], users.current())
    # photos saved before the dressing room existed (or by voice) are made 3D too
    for i in items:
        if i.get("photo") and not garment3d.job(i["id"]) and (garment3d.meta(i["id"]) or {}).get("status") != "ready"                 and (_fd() / i["photo"]).exists():
            garment3d.process_async(i["id"], users.current())
    return len(new)


def closet_op(op: str, args: dict) -> dict:
    """Every change to My Closet. Runs on the PC (vision + GPU, and the single writer of wardrobe.json)."""
    from tools import garment3d
    items = _load("wardrobe.json", [])
    by_id = {i["id"]: i for i in items}
    iid = str(args.get("id", ""))
    if op == "jobs":
        import_pending()
        return {"jobs": {k: v for k, v in garment3d._jobs.items()}}
    if op == "add":
        iid = uuid.uuid4().hex[:6]
        photo = _store_photo(iid, args["photo"]) if args.get("photo") else None
        name = str(args.get("name") or "").strip()[:80] or "New item"
        tpl = args.get("template") if args.get("template") in garment3d.TEMPLATES else None
        cat = garment3d.CATEGORY_OF[garment3d.SLOT_OF[tpl]] if tpl else str(args.get("category") or "")
        row = {"id": iid, "name": name, "category": cat, "colors": str(args.get("colors") or ""), "description": str(args.get("hint") or "")[:300],
               "photo": photo, "added": datetime.now().date().isoformat(), "auto_name": not args.get("name")}
        items.append(row); _save("wardrobe.json", items)
        garment3d.process_async(iid, users.current(), correction={"template": tpl} if tpl else None)
        return {"ok": True, "id": iid}
    if iid not in by_id:
        raise ValueError("That item isn't in your closet.")
    it = by_id[iid]
    if op == "update":
        for k in ("name", "category", "colors", "description"):
            if k in args:
                it[k] = str(args[k])[:300]
        if args.get("season") in ("all", "warm", "cold"):
            it["season"] = args["season"]; it.pop("season_auto", None)
        if "favorite" in args:
            it["favorite"] = bool(args["favorite"])
        if "occasions" in args:
            it["occasions"] = [str(o)[:30] for o in args["occasions"]][:8]
        it.pop("auto_name", None)
        _save("wardrobe.json", items)
        corr = {}
        if args.get("template") in garment3d.TEMPLATES:
            corr["template"] = args["template"]
        if isinstance(args.get("model_colors"), list):
            corr["colors"] = [c for c in args["model_colors"] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(c))][:4]
        if corr:
            m = garment3d.meta(iid) or garment3d.basic_model(it)
            moved = "template" in corr and garment3d.SLOT_OF[corr["template"]] != m.get("slot")
            m.update(corr); m["slot"] = garment3d.SLOT_OF.get(m["template"], m.get("slot")); m.pop("guessed", None)
            garment3d._write_meta(iid, m)
            if moved and it.get("photo"):   # e.g. shoes mistaken for a top: the photo must be cut out again for the new shape
                garment3d.process_async(iid, users.current(), force=True, correction=corr)
        return {"ok": True}
    if op == "photo":   # a better/extra photo: becomes the source, the old one is kept
        if it.get("photo"):
            old = _fd() / it["photo"]
            if old.exists():
                old.rename(old.with_name(f"{iid}-{int(time.time())}.jpg"))
            old.with_name(f"{old.stem}-clothes.jpg").unlink(missing_ok=True)
        it["photo"] = _store_photo(iid, args["photo"])
        _save("wardrobe.json", items)
        garment3d.process_async(iid, users.current(), force=True)
        return {"ok": True}
    if op == "reprocess":
        garment3d.process_async(iid, users.current(), force=True,
                                correction={"template": args["template"]} if args.get("template") in garment3d.TEMPLATES else None)
        return {"ok": True}
    if op == "delete":
        import shutil
        _save("wardrobe.json", [i for i in items if i["id"] != iid])
        if it.get("photo"):
            (_fd() / it["photo"]).unlink(missing_ok=True)
        shutil.rmtree(_fd() / "3d" / re.sub(r"[^\w-]", "", iid), ignore_errors=True)
        return {"ok": True}
    raise ValueError("Unknown closet change.")


def queue_add(args: dict) -> dict:
    """On the phone server while the PC is away: keep the photo and the item until the PC adopts it."""
    iid = uuid.uuid4().hex[:6]
    photo = _store_photo(iid, args["photo"]) if args.get("photo") else None
    pend = _load("wardrobe-pending-server.json", [])
    have = {i["id"] for i in _load("wardrobe.json", [])}
    pend = [p for p in pend if p["id"] not in have] + [{"id": iid, "name": str(args.get("name") or "").strip()[:80] or "New item",
            "category": str(args.get("category") or ""), "colors": "", "description": str(args.get("hint") or "")[:300], "photo": photo,
            "added": datetime.now().date().isoformat(), "auto_name": not args.get("name")}]
    _save("wardrobe-pending-server.json", pend)
    return {"ok": True, "id": iid, "queued": True}


def discover_items() -> list:
    from tools import garment3d
    out = []
    found = {k: v for k, v in _load("garments.json", {}).items() if isinstance(v, dict)}
    for gid, g in sorted(found.items(), key=lambda kv: kv[1].get("added", ""), reverse=True):
        if not g.get("discover"):   # older Studio try-on references (often whole-outfit pins), not store products
            continue
        m = garment3d.meta(gid)
        if m and m.get("texture"):
            m = {**m, "texture": f"fashion/3d/{gid}/{m['texture']}"}
        elif not m:
            tpl = garment3d.guess_template(g.get("description", ""))
            m = {"template": tpl, "slot": garment3d.SLOT_OF[tpl], "colors": garment3d.colors_from_text(g.get("description", "")),
                 "texture": None, "bbox": [0, 0, 1, 1], "status": "basic"}
        out.append({"src": "discover", "id": gid, "name": g.get("title") or g.get("description", "")[:60], "description": g.get("description", ""),
                    "photo": f"fashion/garments/{gid}.jpg", "url": g.get("source", ""), "store": g.get("store") or _store_of(g.get("source", "")),
                    "price": g.get("price", ""), "query": g.get("query", ""), "added": g.get("added", ""), "model": m})
    return out


def basics() -> list:
    from tools import garment3d
    return [{"src": "basic", "id": k, "name": v["name"], "model": {"template": v["template"], "slot": garment3d.SLOT_OF[v["template"]],
             "colors": v["colors"], "texture": None, "bbox": [0, 0, 1, 1], "status": "basic", **({"sole": v["sole"]} if "sole" in v else {})}}
            for k, v in BASICS.items()]


def _store_of(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).replace(".com", "") if m else ""


def find(ref: dict) -> dict | None:
    src, iid = ref.get("src"), ref.get("id")
    pool = closet() if src == "closet" else discover_items() if src == "discover" else basics()
    return next((i for i in pool if i["id"] == iid), None)


# -- the avatar --------------------------------------------------------------------------------------
def avatar_cfg(uid: str | None = None) -> dict:
    uid = uid or users.current()
    rows = [r for r in (_load(f"avatar3d-{h}.json", None) for h in ("pc", "server")) if r]
    if rows:
        return max(rows, key=lambda r: r.get("t", 0))
    female = uid != "owner" and (users.get(uid).get("role") in ("wife", "partner", "daughter") or "maya" in uid)
    return {"sex": "f" if female else "m", "skin": "#7a4a2a" if not female else "#8a5634", "hair": "bob" if female else "fade",
            "hairColor": "#161210", "eyeColor": "#33241b", "beard": "none" if female else "stubble",
            "shape": {"height": .55, "muscle": .45 if not female else .25, "weight": .2, "chest": .25, "nose": .45, "jaw": .55,
                      "lips": .45, "chin": .5, "cheek": .45, "eyes": .45}}


def set_avatar_cfg(cfg: dict) -> dict:
    allowed = {"sex", "skin", "hair", "hairColor", "eyeColor", "beard", "shape", "name"}
    clean = {k: v for k, v in (cfg or {}).items() if k in allowed}
    if "shape" in clean:
        clean["shape"] = {k: max(0.0, min(1.0, float(v))) for k, v in clean["shape"].items()
                          if k in ("height", "muscle", "weight", "chest", "nose", "jaw", "lips", "chin", "cheek", "eyes")}
    cur = {**avatar_cfg(), **clean, "t": time.time()}
    _save(f"avatar3d-{_host()}.json", cur)
    return cur


# -- the look on the avatar right now, and saved looks (per machine, merged) -----------------------------
def current() -> dict:
    rows = [_load(f"current-{h}.json", None) for h in ("pc", "server")]
    rows = [r for r in rows if r]
    return max(rows, key=lambda r: r.get("t", 0)) if rows else {"items": {}, "name": "", "why": "", "t": 0}


def set_current(items: dict, name: str = "", why: str = "", by: str = "app", target: list | None = None) -> dict:
    clean = {s: {"src": r.get("src"), "id": r.get("id")} for s, r in (items or {}).items() if s in SLOTS and r and r.get("id")}
    row = {"items": clean, "name": str(name)[:80], "why": str(why)[:500], "t": time.time(), "by": by}
    if target:
        row["_target"] = list(target)[:2]
    _save(f"current-{_host()}.json", row)
    return row


def looks() -> list:
    rows, deleted = [], set()
    for h in ("pc", "server"):
        d = _load(f"looks-{h}.json", {"looks": [], "deleted": []})
        rows += d.get("looks", []); deleted |= set(d.get("deleted", []))
    seen, out = {}, []
    for r in sorted(rows, key=lambda r: r.get("updated", r.get("created", "")), reverse=True):
        if r["id"] in deleted or r["id"] in seen:
            continue
        seen[r["id"]] = True; out.append(r)
    return out


def _mine():
    return _load(f"looks-{_host()}.json", {"looks": [], "deleted": []})


def save_look(name: str, items: dict, occasion: str = "", thumb: str = "", favorite: bool = False, look_id: str = "") -> dict:
    d = _mine()
    lid = look_id or uuid.uuid4().hex[:8]
    if thumb and thumb.startswith("data:image/"):
        import base64
        (_fd() / "looks").mkdir(parents=True, exist_ok=True)
        raw = base64.b64decode(thumb.split(",", 1)[1])
        if len(raw) < 900_000:
            (_fd() / "looks" / f"{lid}.webp").write_bytes(raw)
            thumb = f"fashion/looks/{lid}.webp"
        else:
            thumb = ""
    old = next((r for r in looks() if r["id"] == lid), {})
    row = {**old, "id": lid, "name": (name or old.get("name") or "My look").strip()[:60], "occasion": occasion or old.get("occasion", ""),
           "items": {s: {"src": r.get("src"), "id": r.get("id")} for s, r in (items or old.get("items", {})).items() if s in SLOTS and r},
           "thumb": thumb or old.get("thumb", ""), "favorite": bool(favorite if look_id == "" else old.get("favorite", favorite)),
           "created": old.get("created") or datetime.now().isoformat(timespec="minutes"), "updated": datetime.now().isoformat(timespec="seconds")}
    d["looks"] = [r for r in d["looks"] if r["id"] != lid] + [row]
    _save(f"looks-{_host()}.json", d)
    return row


def update_look(look_id: str, **changes) -> dict | None:
    old = next((r for r in looks() if r["id"] == look_id), None)
    if not old:
        return None
    d = _mine()
    row = {**old, **{k: v for k, v in changes.items() if k in ("name", "occasion", "favorite")}, "updated": datetime.now().isoformat(timespec="seconds")}
    d["looks"] = [r for r in d["looks"] if r["id"] != look_id] + [row]
    _save(f"looks-{_host()}.json", d)
    return row


def delete_look(look_id: str) -> bool:
    d = _mine()
    d["looks"] = [r for r in d["looks"] if r["id"] != look_id]
    d["deleted"] = list(set(d.get("deleted", [])) | {look_id})[-500:]
    _save(f"looks-{_host()}.json", d)
    return True


def data(jobs: dict | None = None) -> dict:
    from tools import garment3d
    return {"avatar": avatar_cfg(), "closet": closet(jobs), "discover": discover_items(), "basics": basics(), "looks": looks(),
            "current": current(), "templates": garment3d.TEMPLATES, "slot_of": garment3d.SLOT_OF, "occasions": [v[2] for v in OCCASIONS.values()]}


# -- the stylist -------------------------------------------------------------------------------------
def _hsl(hexc: str):
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return hh * 360, ss, ll


def _neutral(hexc: str) -> bool:
    h, s, l = _hsl(hexc)
    return s < .2 or l < .14 or l > .9 or (20 <= h <= 48 and s < .55 and .25 < l < .8) or (200 <= h <= 235 and l < .32)  # greys, black/white, tan/beige/brown, navy/denim


def _close(a: str, b: str, tol: float = 26) -> bool:
    h1, s1, l1 = _hsl(a); h2, s2, l2 = _hsl(b)
    dh = min(abs(h1 - h2), 360 - abs(h1 - h2))
    return dh < tol and abs(l1 - l2) < .22 and abs(s1 - s2) < .35


COLOR_WORDS = {"black": "#17171a", "white": "#f2f1ec", "cream": "#ece3cf", "beige": "#d6c3a0", "tan": "#c39463", "brown": "#6b4628",
               "grey": "#8f9196", "gray": "#8f9196", "navy": "#1f2a44", "blue": "#2f5d9e", "denim": "#3e5b80", "red": "#b3262e",
               "green": "#3f7a4a", "olive": "#6b6d3a", "pink": "#e8a3b3", "burgundy": "#6a1f2b", "yellow": "#e3c13c", "orange": "#d9772b",
               "purple": "#6e4a8e", "khaki": "#bba67a", "charcoal": "#3a3c41"}
SLOT_WORDS = {"top": r"shirt|tee|t-shirt|top|polo|hoodie|sweater|tank|blouse", "outer": r"jacket|blazer|coat|outerwear|layer",
              "bottom": r"pants|jeans|trousers|joggers|shorts|bottoms?|skirt|chinos|sweatpants", "shoes": r"shoes|sneakers|boots|kicks|loafers|trainers",
              "hat": r"hat|cap|beanie", "watch": r"watch", "neck": r"chain|necklace", "eyes": r"glasses|shades|sunglasses"}


def parse_request(text: str) -> dict:
    t = (text or "").lower()
    plain = re.sub(r"\b(more|less|a bit|bit|slightly)\s+(formal|casual|classy|dressy|relaxed)\b", " ", t)   # "more formal" adjusts, it isn't an occasion
    occ = next((k for k, rx in OCC_WORDS if re.search(rx, plain)), None)
    want = {}
    for slot, rx in SLOT_WORDS.items():
        for m in re.finditer(rf"\b((?:{'|'.join(COLOR_WORDS)})[\w\s-]{{0,12}}?)?\b({rx})\b", t):
            col = next((c for c in COLOR_WORDS if m.group(1) and c in m.group(1)), None)
            want.setdefault(slot, {"color": col, "word": m.group(2)})
    return {"occasion": occ, "want": want, "n": 5 if re.search(r"\bfive\b|\b5\b", t) else 3 if re.search(r"\bthree\b|\b3\b|\bfew\b|options|combinations", t) else 1,
            "online": bool(re.search(r"online|on the internet|from stores?|to buy|shop(ping)?", t)),
            "more_formal": bool(re.search(r"more formal|dressier|classier|fancier|smarter|dress (it )?up", t)),
            "more_casual": bool(re.search(r"more casual|dress (it )?down|relaxed|chill(er)?|less formal", t)),
            "change": next((s for s, rx in SLOT_WORDS.items() if re.search(rf"\b(change|swap|switch|different|another|new|other)\b[^.?!]{{0,20}}\b({rx})\b", t)), None),
            "remove": next((s for s, rx in SLOT_WORDS.items() if re.search(rf"\b(remove|take off|lose|drop|without|no)\b[^.?!]{{0,12}}\b({rx})\b", t)), None),
            "better": bool(re.search(r"\b(better|something else|another one|another look|next one|different outfit|try again)\b", t))}


def _score(items: dict, target: tuple) -> tuple[float, list[str]]:
    lo, hi = target
    notes, s = [], 0.0
    models = {slot: it["model"] for slot, it in items.items()}
    for slot, m in models.items():
        f = FORMALITY.get(m["template"], 2)
        s -= max(0, lo - f, f - hi) * 1.6
    mains = {slot: m["colors"][0] for slot, m in models.items() if m.get("colors")}
    chroma = [c for c in mains.values() if not _neutral(c)]
    if len(chroma) <= 1:
        s += 1.2
    for i, a in enumerate(chroma):
        for b in chroma[i + 1:]:
            h1 = _hsl(a)[0]; h2 = _hsl(b)[0]; dh = min(abs(h1 - h2), 360 - abs(h1 - h2))
            s += .6 if dh < 30 else .3 if 150 < dh < 210 else -.9
    if "top" in mains and "bottom" in mains:
        dl = abs(_hsl(mains["top"])[2] - _hsl(mains["bottom"])[2])
        s += .6 if dl > .2 else -.2
    # a trim or accent colour echoed elsewhere is what makes an outfit look intentional (one or two echoes, not more)
    for sa, ma in models.items():
        for accent in ma.get("colors", [])[1:3]:
            for sb, mb in models.items():
                if sb != sa and mb.get("colors") and _close(accent, mb["colors"][0]):
                    notes.append(f"the {_color_name(accent)} on the {_slot_word(sa, ma)} picks up the {_slot_word(sb, mb)}")
    s += min(1.3, .7 * len(notes))
    if "outer" in models and "top" in models and _close(mains.get("outer", "#000"), mains.get("top", "#fff"), 14):
        s -= .5
    for it in items.values():
        if it["src"] == "basic":
            s -= .7
    return s, notes


def _color_name(hexc: str) -> str:
    best, bd = "neutral", 1e9
    for name, c in COLOR_WORDS.items():
        a = [int(hexc[i:i + 2], 16) for i in (1, 3, 5)]; b = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
        d = sum((x - y) ** 2 for x, y in zip(a, b))
        if d < bd:
            best, bd = name, d
    return best


def _slot_word(slot, m):
    from tools import garment3d  # noqa: F401
    return {"top": "top", "outer": "jacket", "bottom": "bottoms", "shoes": "shoes", "hat": "hat", "watch": "watch", "neck": "chain", "eyes": "glasses"}.get(slot, slot) \
        if m.get("template") not in ("hightops", "sneakers", "boots", "jeans", "joggers", "hoodie", "blazer", "polo", "tee") else \
        {"hightops": "high-tops", "sneakers": "sneakers", "boots": "boots", "jeans": "jeans", "joggers": "joggers", "hoodie": "hoodie",
         "blazer": "blazer", "polo": "polo", "tee": "tee"}[m["template"]]


GENERIC = {"top", "tops", "shoes", "kicks", "trainers", "bottom", "bottoms", "pants", "layer", "outerwear", "hat", "watch", "chain",
           "necklace", "glasses", "shades", "sunglasses", "cap", "beanie"}
FAMILY = [{"tee", "longsleeve", "polo", "shirt", "tank", "sweater"}, {"hoodie", "zip_hoodie", "sweater"}, {"sneakers", "hightops"},
          {"jacket", "blazer", "coat", "zip_hoodie", "vest"}, {"trousers", "jeans", "joggers"}]


def _want_templates(word: str) -> set | None:
    from tools import garment3d
    if not word or word in GENERIC:
        return None
    tpl = garment3d.guess_template(word)
    fam = next((f for f in FAMILY if tpl in f and tpl not in ("jeans", "joggers", "hightops", "blazer", "polo")), None)
    return fam or {tpl}


def _matches(it: dict, want: dict | None) -> bool:
    if not want:
        return True
    tpls = _want_templates(want.get("word", ""))
    if tpls and it["model"]["template"] not in tpls:
        return False
    if want.get("color"):
        c = COLOR_WORDS[want["color"]]
        if not any(_close(c, x, 30) for x in it["model"].get("colors", [])[:2]) and want["color"] not in (it.get("name", "") + it.get("colors_text", "")).lower():
            return False
    return True


def _describe_want(want: dict) -> str:
    return " ".join(x for x in (want.get("color"), want.get("word")) if x)


def suggest(request: str = "", base: dict | None = None, n: int | None = None, include_discover: bool = False, avoid: list | None = None) -> list:
    """Outfits from the person's real closet that fit the request; each is {name, occasion, items, why}.
    Plain basics only stand in for what the closet lacks (they score lower and are always labelled)."""
    req = parse_request(request)
    pool = closet() + (discover_items() if include_discover or req["online"] else [])
    by_slot: dict[str, list] = {}
    for it in pool:
        by_slot.setdefault(it["model"]["slot"], []).append(it)
    spare: dict[str, list] = {}
    for b in basics():
        spare.setdefault(b["model"]["slot"], []).append(b)
    cur = base or {}
    cur_items = cur.get("items") or {}
    occ = req["occasion"]
    if not occ and cur.get("_target"):
        target = tuple(cur["_target"])
    else:
        occ = occ or "casual"
        target = OCCASIONS[occ][:2]
    if req["more_formal"]:
        target = (target[0] + 1.1, min(5.2, target[1] + 1.4))
        occ = {"casual": "smart casual", "smart casual": "work", "streetwear": "smart casual", None: "smart casual"}.get(occ, occ)
    if req["more_casual"]:
        target = (max(0, target[0] - 1.2), max(1.2, target[1] - 1.2))
        occ = {"formal": "smart casual", "work": "smart casual", "church": "smart casual", None: "casual"}.get(occ, occ)
    missing = []

    def options(slot):
        want = req["want"].get(slot)
        mine = [it for it in by_slot.get(slot, []) if _matches(it, want)]
        stand_ins = [b for b in spare.get(slot, []) if _matches(b, want)]
        if want and not mine:
            missing.append(_describe_want(want))
            return stand_ins or by_slot.get(slot, []) or spare.get(slot, [])
        return mine if (want or slot == "outer") else mine + stand_ins

    # adjustments keep what's on the avatar and change only what was asked about
    adjusting = bool(cur_items) and (req["change"] or req["remove"] or (req["want"] and not req["occasion"]))
    fixed = {}
    if adjusting:
        for slot, ref in cur_items.items():
            if slot in (req["change"], req["remove"]) or slot in req["want"]:
                continue
            it = find(ref)
            if it:
                fixed[slot] = it
    choice = {}
    for slot in ("top", "bottom", "shoes", "outer"):
        if slot in fixed:
            choice[slot] = [fixed[slot]]
            continue
        opts = options(slot)
        if req["change"] == slot and cur_items.get(slot):
            opts = [o for o in opts + spare.get(slot, []) if o["id"] != cur_items[slot]["id"]] or opts
        choice[slot] = opts
    targeted = req["change"] == "outer" or "outer" in req["want"]
    if req["remove"] == "outer" or (adjusting and "outer" not in fixed and not targeted):
        choice["outer"] = [None]
    elif "outer" not in fixed and not req["want"].get("outer") and req["change"] != "outer":
        choice["outer"] = [None] + choice["outer"]
    if not choice["top"] or not choice["shoes"]:
        choice["top"] = choice["top"] or spare["top"]; choice["shoes"] = choice["shoes"] or spare["shoes"]
    if not choice["bottom"]:
        choice["bottom"] = spare["bottom"]
    extras = {s: fixed[s] for s in ("hat", "watch", "neck", "eyes") if s in fixed}
    combos = []
    for t in choice["top"][:14]:
        for b in ([None] if t["model"]["template"] == "dress" else choice["bottom"][:14]):
            for sh in choice["shoes"][:10]:
                for o in choice["outer"][:8]:
                    items = {"top": t, "shoes": sh, **({"bottom": b} if b else {}), **({"outer": o} if o else {}), **extras}
                    sc, notes = _score(items, target)
                    if o:
                        sc += .3 if target[0] >= 2.2 else -.2
                    combos.append((sc, items, notes))
    combos.sort(key=lambda x: -x[0])
    avoid_keys = {json.dumps(a, sort_keys=True) for a in (avoid or []) if a}
    out, used_pairs = [], set()
    want_n = n or req["n"]
    for sc, items, notes in combos:
        refs = {s: {"src": it["src"], "id": it["id"]} for s, it in items.items()}
        key = json.dumps(refs, sort_keys=True)
        pair = (items["top"]["id"], items["bottom"]["id"] if items.get("bottom") else None)
        if key in avoid_keys or (want_n > 1 and pair in used_pairs):
            continue
        used_pairs.add(pair)
        label = OCCASIONS[occ][2] if occ in OCCASIONS else (cur.get("name") or "").split(" · ")[0] or "Outfit"
        mains = [items[s]["model"]["colors"][0] for s in ("top", "bottom") if s in items and items[s]["model"].get("colors")]
        name = f"{label} · " + " & ".join(_color_name(c).capitalize() for c in mains) if mains else label
        pieces = [items[s]["name"] for s in ("outer", "top", "bottom", "shoes", "hat", "watch", "neck", "eyes") if s in items]
        why = (", ".join(pieces[:-1]) + " and " + pieces[-1] if len(pieces) > 1 else pieces[0]) + "."
        if notes:
            why += " " + notes[0][0].upper() + notes[0][1:] + "."
        if missing:
            why += f" You don't have {' or '.join(dict.fromkeys(missing))} saved, so a plain stand-in is shown — add yours with a photo, or ask me to find some online."
        n_basic = sum(it["src"] == "basic" for it in items.values()) - len(missing)
        if n_basic > 0:
            why += " Plain basics (marked) fill in where your closet has nothing that fits the occasion yet."
        if any(it["src"] == "discover" for it in items.values()):
            why += " Includes pieces found online, not in your closet."
        out.append({"name": name, "occasion": label, "items": refs, "why": why, "score": round(sc, 2), "_target": list(target)})
        if len(out) >= want_n:
            break
    return out


# -- online discovery ------------------------------------------------------------------------------------
RECIPES = {   # pieces to look for, by occasion (the request's own words are added)
    "luxury casual": ["cream cashmere crew neck sweater", "tailored charcoal wool trousers", "white leather minimalist sneakers"],
    "streetwear": ["oversized graphic hoodie", "relaxed black cargo pants", "chunky high top sneakers"],
    "formal": ["navy slim fit suit blazer", "white dress shirt", "charcoal dress trousers", "black leather oxford shoes"],
    "church": ["navy blazer", "light blue oxford shirt", "grey wool trousers", "brown leather loafers"],
    "date": ["black knit polo shirt", "dark slim jeans", "brown suede chelsea boots"],
    "work": ["white oxford shirt", "navy chinos", "brown leather derby shoes"],
    "gym": ["black performance t-shirt", "grey jogger sweatpants", "white running shoes"],
    "casual": ["heavyweight white t-shirt", "light wash straight jeans", "white canvas sneakers"],
    "smart casual": ["camel overshirt jacket", "black crew neck tee", "beige chinos", "white leather sneakers"],
    "party": ["black leather jacket", "white tee", "black slim jeans", "black boots"],
}
_disc = {"state": "idle", "detail": "", "query": "", "items": [], "look": None, "t": 0}


def discover_status() -> dict:
    return dict(_disc)


def discover(query: str, uid: str | None = None) -> dict:
    """Search stores for a look, fetch real product photos, make them wearable, and assemble a preview look.
    Runs on the PC (web search CLI + vision + GPU-free OpenCV); products land in Discover, never in My Closet."""
    from tools import garment3d, reach, studio
    uid = uid or users.current()
    req = parse_request(query)
    occ = req["occasion"] or "casual"
    clean = re.sub(r"\b(online|on the internet|from stores?|to buy|shop(ping)? for|find( me)?|show me|search( for)?)\b", " ", query, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" ,.") or query
    if req["want"] and not req["occasion"] and not re.search(r"\b(outfit|look|fit|combination)s?\b", query, re.I):
        pieces = [clean]   # one piece asked for: search for exactly that
    else:
        pieces = list(RECIPES.get(occ, RECIPES["casual"]))
        for slot, w in req["want"].items():   # "with white sneakers" -> swap that piece
            words = f"{w['color'] or ''} {w['word']}".strip()
            pieces = [p for p in pieces if not re.search(SLOT_WORDS[slot], p)] + [words]
    _disc.update(state="working", detail="Searching stores…", query=query, items=[], look=None, t=time.time())
    found, search_failed = [], 0
    with users.acting_as(uid):
        meta_all = _load("garments.json", {})
        for k, piece in enumerate(pieces[:5]):
            _disc["detail"] = f"Looking for {piece} ({k + 1}/{min(5, len(pieces))})…"
            try:
                hits = product_search(piece)
            except Exception:
                hits, search_failed = [], search_failed + 1
            got = None
            for h in hits[:5]:
                try:
                    img = product_image(h["url"])
                except Exception:
                    continue
                if min(img.size) < 200:
                    continue
                gid = "g" + uuid.uuid4().hex[:5]
                (_fd() / "garments").mkdir(parents=True, exist_ok=True)
                img.convert("RGB").save(_fd() / "garments" / f"{gid}.jpg", quality=90)
                meta_all[gid] = {"description": piece, "source": h["url"], "store": h.get("store", ""), "title": (h.get("title") or piece)[:70],
                                 "query": query, "added": datetime.now().strftime("%Y%m%d-%H%M%S"), "discover": True}
                _save("garments.json", meta_all)
                try:
                    _process_product(gid, piece, _fd() / "garments" / f"{gid}.jpg")
                except Exception as e:
                    print(f"[discover] process {gid}: {e}")
                got = gid
                break
            if got:
                found.append(got)
        items = [i for i in discover_items() if i["id"] in found]
        look = {}
        for it in items:
            look.setdefault(it["model"]["slot"], {"src": "discover", "id": it["id"]})
        if look:
            look = {**current().get("items", {}), **look}   # the found pieces go on over what was already worn
            set_current(look, f"{OCCASIONS.get(occ, ('', '', 'Online'))[2]} · found online", f"Found online for “{query}”. Tap a piece for its store link.", by="discover")
    if found:
        detail = f"Found {plural(len(found), 'piece')}."
    elif search_failed and search_failed == min(5, len(pieces)):
        detail = "Couldn't reach the web search, so I couldn't look in any stores. Try again in a bit."
    else:
        detail = "Couldn't find usable product photos. Try different words."
    _disc.update(state="done" if found else "error", detail=detail, items=found, look=look if found else None, t=time.time())
    return discover_status()


PRODUCT_URL = re.compile(r"/products?/|/p/|/dp/|/pd/|/item/|/itm/|/prd/|-p-\d|/sku/|\d{6,}|/[a-z0-9-]+-[a-z0-9-]+-\d+\.html", re.I)
NOT_PRODUCT = re.compile(r"/search|[?&](k|q|query|search)=|/s\?|/category/|/categories/|/c/|/shop/?$|/collections/[^/]+/?$|/ideas/|/blog/", re.I)


def product_search(piece: str) -> list:
    """Single product pages (not category or search pages) for one piece, best first: [{store, title, url}]."""
    from tools import reach
    hits, errors, searched = [], [], 0
    for q, objective in ((f"{piece} product page", "a single clothing product page from an online store, with price and product photo"),
                         (f"{piece} buy online", "product pages to purchase")):
        try:
            out = reach.search_web_free(q, objective=objective, results=10)
            searched += 1
        except Exception as e:
            errors.append(e)
            continue
        for u in re.findall(r"https?://[^\s\)\]\">]+", out):
            host = re.sub(r"https?://(www\.)?", "", u).split("/")[0].lower()
            if any(b in host for b in ("google.", "bing.", "pinterest.", "youtube.", "facebook.", "instagram.", "wikipedia.", "reddit.", "tiktok.")):
                continue
            if NOT_PRODUCT.search(u) or any(h["url"] == u for h in hits):
                continue
            slug = re.sub(r"\.html?$", "", u.rstrip("/").split("/")[-1].split("?")[0], flags=re.I)
            title = re.sub(r"[-_]+", " ", slug).strip()
            hits.append({"store": _store_of(u), "title": title if len(re.sub(r"[^A-Za-z]", "", title)) >= 4 else piece, "url": u,
                         "product": bool(PRODUCT_URL.search(u))})
        if sum(h["product"] for h in hits) >= 3:
            break
    if errors and not searched:
        raise errors[-1]   # the search itself failed (e.g. offline) — not "no results"; the caller tells them apart
    return sorted(hits, key=lambda h: not h["product"])[:6]


def product_image(url: str):
    """The product photo of a store page: og:image, else the page's structured product data."""
    import html as _html
    from io import BytesIO
    from urllib.parse import urljoin

    from PIL import Image

    from tools import studio
    try:
        return studio._download_image(url)
    except ValueError:
        pass
    data, ctype = studio._get(url)
    page = data.decode("utf-8", errors="ignore")
    m = re.search(r'"image"\s*:\s*(?:\[\s*)?(?:\{[^}]*?"url"\s*:\s*)?"(https?:[^"]+)"', page) or \
        re.search(r"""<img[^>]+(?:data-src|src)=["'](https?:[^"']+\.(?:jpe?g|png|webp)[^"']*)""", page, re.I)
    if not m:
        raise ValueError("no product photo on that page")
    raw, _ = studio._get(urljoin(url, _html.unescape(m.group(1)).replace("\\/", "/")))
    img = Image.open(BytesIO(raw)); img.load()
    return img.convert("RGB")


def _process_product(gid: str, desc: str, path):
    from tools import garment3d
    tpl = garment3d.guess_template(desc)
    tex, bbox, palette, mask = garment3d.prepare_texture(path, None, white_bg=True)
    d = garment3d.item_dir(gid)
    tex.save(d / "texture.jpg", quality=90)
    m = {"template": tpl, "slot": garment3d.SLOT_OF[tpl], "colors": palette[:3] or garment3d.colors_from_text(desc), "texture": "texture.jpg",
         "bbox": bbox, "status": "ready", "source": "product photo", "processed": time.strftime("%Y-%m-%d %H:%M")}
    if m["slot"] == "shoes":
        m["sole"] = garment3d.sole_color(path, bbox, mask) or "#f1efe9"
    (d / "meta.json").write_text(json.dumps(m, indent=1), encoding="utf-8")


def discover_async(query: str, uid: str) -> None:
    """Search in the background. The voice reply promised pieces "in about a minute", so ALWAYS close the loop:
    a notification when they're ready, and one when it didn't work (otherwise the person just waits for nothing)."""
    def run():
        from core import notify
        try:
            discover(query, uid)
        except Exception as e:
            _disc.update(state="error", detail=f"Search failed: {str(e)[:120]}", t=time.time())
        try:
            if _disc["state"] == "done":
                n = len(_disc["items"])
                notify.notify("Outfit found online", f"{plural(n, 'piece')} for “{query}” {'is' if n == 1 else 'are'} on "
                              "your avatar in the dressing room.", uid, "jarvis")
            else:
                notify.notify("Couldn't find that outfit online", f"For “{query}”: {_disc['detail']}", uid, "jarvis")
        except Exception as e:
            print(f"[discover] notify failed: {type(e).__name__}: {e}")
    _disc.update(state="working", detail="Starting…", query=query, items=[], look=None, t=time.time())
    threading.Thread(target=run, daemon=True, name="discover").start()


# -- voice and chat ----------------------------------------------------------------------------------------
def style(request: str) -> str:
    """Apply the stylist's best answer to the avatar and say what it is."""
    req = parse_request(request)
    cur = current()
    if req["online"]:
        from core import pcbridge
        if pcbridge.local_pc():
            discover_async(request, users.current())
        else:
            try:
                person = users.current()
                with users.acting_as("owner"):   # the server's link to the PC is the owner's; the person rides along
                    pcbridge.json_request("/api/pc/dress/discover", "POST", {"query": request, "person": person})
            except Exception as e:
                return f"I can't search stores right now: {e}"
        return "On it — searching stores. The pieces will appear on your avatar in the dressing room in about a minute, with their store links."
    base = {"items": cur.get("items", {}), "_target": cur.get("_target")} if cur.get("items") else None
    from tools import fitting
    looks_ = suggest(request + fitting.weather_words(request), base=base, avoid=[cur.get("items")] if req["better"] and cur.get("items") else None)
    if not looks_:
        return "Your closet needs a few more pieces first: add clothes in the Wardrobe (snap a photo of each)."
    top = looks_[0]
    set_current(top["items"], top["name"], top["why"], by="stylist", target=top["_target"])
    extra = f" I lined up {len(looks_) - 1} more in the Stylist tab." if len(looks_) > 1 else ""
    _show_wardrobe()
    return f"{top['name']}: {top['why']} It's on your avatar now, and on the Wardrobe page with a one-tap try-on.{extra}"


def _show_wardrobe() -> None:
    """At the PC, bring the Wardrobe page up so the answer is seen as well as heard (the phone shows it when opened)."""
    try:
        from core import hud, pcbridge
        if pcbridge.local_pc():
            hud.wardrobe()
    except Exception:  # noqa: BLE001
        pass


@tool("Style an outfit from the person's real closet and put it on their 3D avatar in the dressing room: 'make me a "
      "casual outfit', 'something classy with my white sneakers', 'an outfit for church', 'what can I wear with these jeans', "
      "'change the shoes', 'make it more formal', 'try a black jacket', 'show me something better'. Add 'online' to search stores.",
      {"request": {"type": "string", "description": "What they asked for, in their words"}}, direct=True)
def style_outfit(request: str) -> str:
    return style(request)


@tool("Find clothes online for a look ('a luxury casual outfit', 'white leather sneakers') and preview them on the 3D avatar, "
      "with store links. They stay in Discover, separate from what the person owns.",
      {"query": {"type": "string"}}, direct=True, slow=True)
def discover_outfit(query: str) -> str:
    return style(query + " online")
