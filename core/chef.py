"""The chef: a photo of what he bought (or a receipt, a plate, a label) -> what's there -> meals the family can cook now.

Free paths only: the local vision model on this machine, else the PC's GPU vision over the private relay (the laptop,
which serves the phone, has none), the same chain as /api/eyes. Receipts keep the existing local OCR path. When nothing in
the dish library fits what is on hand, the chef makes up one "scraps" recipe from exactly those things (local model),
saved so it can be liked, cooked again and shown like any other dish.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from datetime import datetime

from core import nutri, storage

KINDS = ("auto", "groceries", "receipt", "plate", "label")
import threading  # noqa: E402

_files = threading.Lock()   # the snap index and the chef's creations are read-modify-write from several requests
MAX_SNAPS = 200

_SEE_SYSTEM = ("You are the eyes of a family chef. Look at the photo and list the FOOD in it. Reply with ONLY JSON, no "
               "prose: {\"kind\": \"groceries\"|\"plate\"|\"label\"|\"receipt\"|\"other\", \"items\": [{\"name\": "
               "string, \"qty\": string}], \"dish\": string, \"label\": {\"serving\": string, \"kcal\": number, "
               "\"protein_g\": number, \"carbs_g\": number, \"fat_g\": number}}. Groceries/fridge/pantry: one item per "
               "product, plain names a cook uses ('chicken thighs', 'green plantains', 'long-grain rice', 'red kidney "
               "beans', 'epis'), qty if visible ('3 lb', '2 cans'). Plate: the dish name in 'dish' and its parts as "
               "items with rough portions. Label: copy the nutrition facts per serving into 'label'. Never invent "
               "items you cannot see; leave a field empty when unsure.")


def see(image_data_url: str, question: str = "") -> str:
    """Ask the free vision model about one photo: this machine's, else the PC's over the relay. Raises when neither."""
    from core import pcbridge, users, vision
    q = question or "List the food in this photo as JSON."
    if vision.available():
        return vision.describe([image_data_url], q, system=_SEE_SYSTEM)
    if not pcbridge.local_pc() and (users.get(users.current()) or {}).get("role") == "owner":
        reply = pcbridge.json_request("/api/pc/eyes", "POST", {"images": [image_data_url], "text": q,
                                                                "system": _SEE_SYSTEM}).get("reply")
        if reply:
            return reply
    raise RuntimeError("vision unavailable")


def _json_in(text: str) -> dict:
    text = (text or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        from core import offline
        v = offline.loads_lenient(m.group(0))
    except Exception:  # noqa: BLE001
        try:
            v = json.loads(m.group(0))
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _clean_items(items) -> list:
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        name = re.sub(r"\s+", " ", str(it.get("name", ""))).strip(" .,-")[:60]
        if not name or name.lower() in seen or len(name) < 2:
            continue
        seen.add(name.lower())
        out.append({"name": name, "qty": str(it.get("qty", "") or "")[:30]})
    return out[:60]


def _store_snap(data: bytes, kind: str) -> tuple[str, str]:
    """Save a downscaled JPEG (max 1280 px) of the photo in the household food gallery. Returns (id, data URL)."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=84)
    jpg = buf.getvalue()
    sid = datetime.now().strftime("%Y%m%d-%H%M%S-") + hashlib.sha1(jpg).hexdigest()[:6]
    path = storage.path(f"{nutri.SNAP_DIR}/{sid}.jpg")
    path.write_bytes(jpg)
    import base64
    return sid, "data:image/jpeg;base64," + base64.b64encode(jpg).decode()


def _index(entry: dict) -> None:
    with _files:
        _index_locked(entry)


def _index_locked(entry: dict) -> None:
    idx = storage.load(f"{nutri.SNAP_DIR}/index.json", [])
    idx.append(entry)
    for old in idx[:-MAX_SNAPS]:
        try:
            storage.path(f"{nutri.SNAP_DIR}/{old['id']}.jpg").unlink(missing_ok=True)
        except OSError:
            pass
    storage.save(f"{nutri.SNAP_DIR}/index.json", idx[-MAX_SNAPS:])


def snap(data: bytes, kind: str = "auto") -> dict:
    """One photo -> {"id", "kind", "items", "dish", "label", "meals", "scraps", "note"}. Nothing goes into the pantry or
    the food log here: the page shows what was seen and he confirms (POST /api/kitchen/pantry, /api/kitchen/eat)."""
    from tools import pantry
    kind = kind if kind in KINDS else "auto"
    sid, url = _store_snap(data, kind)
    seen: dict = {}
    note = ""
    if kind == "receipt":
        try:
            from PIL import Image
            from core import pcvision
            lines, _ = pcvision.ocr_image(Image.open(io.BytesIO(data)))
            seen = {"kind": "receipt", "items": pantry.parse_receipt("\n".join(ln["text"] for ln in lines))}
        except Exception:  # noqa: BLE001 - OCR missing on this machine: the vision model reads it instead
            seen = {}
    if not seen.get("items"):
        try:
            hint = {"groceries": "These are groceries we just bought (or our fridge / pantry).",
                    "plate": "This is a plate of food about to be eaten.", "label": "This is a nutrition facts label.",
                    "receipt": "This is a grocery receipt: list the food products bought."}.get(kind, "")
            if kind in ("plate", "auto"):
                # live 2026-11-20: griot came back as "Vietnamese-style fried pork belly"; give it the family's own dishes
                names = sorted({d["name"] for d in nutri.library().values() if not d.get("chef_scraps")})
                hint += (" This family cooks Haitian and Caribbean food. If the plate is one of these dishes, put its exact "
                         "name in 'dish': " + "; ".join(names) + ".")
            seen = _json_in(see(url, (hint + " List the food as JSON.").strip()))
        except Exception:  # noqa: BLE001
            from core import users
            owner = (users.get(users.current()) or {}).get("role") == "owner"
            note = ("My eyes are offline right now (the PC may be asleep). The photo is saved; try again in a minute."
                    if owner else "Reading photos runs on the owner's PC, which this phone can't use. The photo is saved.")
    items = _clean_items(seen.get("items"))
    kind_seen = seen.get("kind") if seen.get("kind") in KINDS else kind
    entry = {"id": sid, "kind": kind_seen if kind == "auto" else kind, "at": datetime.now().isoformat(timespec="seconds"),
             "items": [i["name"] for i in items]}
    _index(entry)
    result = {"id": sid, "kind": entry["kind"], "items": items, "dish": str(seen.get("dish") or "")[:80],
              "label": seen.get("label") if isinstance(seen.get("label"), dict) else None,
              "thumb": f"/api/kitchen/snap/{sid}.jpg", "note": note, "meals": [], "scraps": None}
    if entry["kind"] in ("groceries", "receipt", "auto", "other") and items:
        result.update(meals_from([i["name"] for i in items]))
    if entry["kind"] == "plate" and result["dish"]:
        result["match"] = match_dish(result["dish"], [i["name"] for i in items])
        if result["match"]:
            result["match_dish"] = nutri.dishes_for([result["match"]]).get(result["match"])
    return result


def meals_from(extra: list) -> dict:
    """What to cook now from the pantry plus these just-seen items: library dishes first; a scraps recipe when the
    best of them still misses key ingredients."""
    from tools import pantry
    have = pantry.list_items()
    meals = nutri.cook_now(have, limit=6, extra=extra)
    lib = nutri.library()
    full = [m for m in meals if not m["missing"]]
    scraps = None
    if len(full) < 2:
        scraps = make_scraps([p["name"] for p in have] + list(extra))
    t = nutri.taste()
    full_dish = lambda i: {**lib[i], "photo": nutri.photo_url(i), "taste": nutri.taste_of(i, t)}  # noqa: E731
    return {"meals": [{**m, "dish": full_dish(m["dish"]), "id": m["dish"], "name": lib[m["dish"]]["name"],
                       "photo": nutri.photo_url(m["dish"])} for m in meals],
            "scraps": scraps}


_PROTEINS = {"pork", "chicken", "beef", "goat", "fish", "shrimp", "turkey", "egg", "conch", "salmon", "oxtail", "snapper",
             "herring", "saltfish", "cod", "tuna", "sardine", "lambi", "mackerel", "lamb", "sausage", "hotdog"}


def match_dish(name: str, items: list | None = None) -> str | None:
    """The library dish a plate photo most likely is (for 'log it'): its exact name first, else the dish sharing the
    most words with the name and the parts seen, and at least two of them, so one stray word ("pickled") never decides."""
    lib = nutri.library()
    key = (name or "").strip().casefold()
    for d in lib.values():
        if key and key in (d["name"].casefold(), str(d.get("english", "")).casefold()):
            return d["id"]
    want = nutri.words(name) | set().union(*(nutri.words(i) for i in items or []))
    meat = want & _PROTEINS
    best, score = None, 0
    for d in lib.values():
        have = nutri.words(d["name"]) | nutri.words(d.get("english", "")) | \
            set().union(*(nutri.words(i.get("item", "")) for i in d.get("ingredients") or [] if isinstance(i, dict) and i.get("key")))
        if meat and not (meat & have):
            continue                     # pork on the plate is never the fish dish, whatever else they share
        s = len(want & have) + len(meat & have)
        if s > score:
            best, score = d["id"], s
    return best if score >= 2 else None


_CHEF_SYSTEM = ("You are a Haitian home chef cooking for a family of 5 (two adults who train hard, a toddler and two "
                "more). Make ONE tasty, filling meal using ONLY the ingredients listed plus basic staples (salt, pepper, "
                "oil, garlic, onion, lime, thyme, epis, bouillon, water). Haitian flavours first. Efficient: at most 6 "
                "steps, one pot or one pan when possible. Reply with ONLY JSON: {\"name\": string, \"english\": string, "
                "\"ingredients\": [{\"item\": string, \"qty\": string}], \"steps\": [string], \"total_min\": number, "
                "\"toddler\": string, \"macros\": {\"kcal\": number, \"protein_g\": number, \"carbs_g\": number, "
                "\"fat_g\": number}}. Macros are per adult portion (the meal makes 5 portions).")


def make_scraps(on_hand: list) -> dict | None:
    """The chef's own recipe from exactly what is on hand. Saved (household/chef-creations.json) so it can be liked and
    cooked again; None when the local model can't be reached."""
    names = [n for n in dict.fromkeys(str(x).strip() for x in on_hand) if n][:40]
    if len(names) < 2:
        return None
    from tools import pantry
    try:
        raw = pantry._local_chat(_CHEF_SYSTEM, "On hand: " + ", ".join(names), json_mode=True, max_tokens=900,
                                 temperature=0.5)
    except Exception:  # noqa: BLE001
        return None
    r = _json_in(raw)
    if not r.get("name") or not isinstance(r.get("steps"), list) or not isinstance(r.get("ingredients"), list) \
            or not [s for s in r["steps"] if str(s).strip()] or not r["ingredients"]:
        return None
    m = r.get("macros") if isinstance(r.get("macros"), dict) else {}
    macros = {k: max(0, int(float(m.get(k) or 0))) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
    did = "chef-" + re.sub(r"[^a-z0-9]+", "-", str(r["name"]).lower()).strip("-")[:40] + "-" + \
        hashlib.sha1(json.dumps(names).encode()).hexdigest()[:5]
    d = {"id": did, "name": str(r["name"])[:60], "english": str(r.get("english") or "")[:80], "cuisine": "Haitian",
         "category": "main", "meals": ["lunch", "dinner"], "servings": 5, "portion": "Adults 1 plate; toddler a small plate",
         "active_min": None, "total_min": int(float(r.get("total_min") or 30)), "difficulty": 1,
         "ingredients": [{"item": str(i.get("item", ""))[:60], "qty": str(i.get("qty", ""))[:30], "key": True,
                          "aisle": "pantry"} for i in r["ingredients"] if isinstance(i, dict) and i.get("item")][:20],
         "steps": [str(s)[:300] for s in r["steps"] if str(s).strip()][:6], "macros": macros, "tags": ["scraps"],
         "toddler": str(r.get("toddler") or "Take the toddler's portion out before adding pepper; no added salt.")[:200],
         "chef_scraps": True, "made": datetime.now().isoformat(timespec="seconds"), "estimate": True,
         "photo_prompt": f"Appetizing overhead food photograph of {r['name']} served family-style in a simple ceramic "
                         "bowl on a warm wooden table, soft natural window light, editorial cookbook style, no text"}
    with _files:
        extra = storage.load("household/chef-creations.json", {})
        extra[did] = d
        if len(extra) > 150:
            for k in sorted(extra, key=lambda k: extra[k].get("made", ""))[:-150]:
                extra.pop(k, None)
        storage.save("household/chef-creations.json", extra)
    nutri.forget_cache()
    return d
