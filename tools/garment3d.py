"""Real clothes -> wearable 3D garments for the dressing room (app/dress3d.js).

For each wardrobe item with a photo:
  1. the local vision model (Qwen3-VL in Ollama, on the PC) identifies it: garment type -> 3D template, colours,
     pattern, details, and where the item is in the picture;
  2. FLUX makes a clean front-facing product shot when the photo shows it being worn (tools/studio._clothes_only);
  3. OpenCV cuts the garment out, measures its colours, and floods its own colours into the background, so the
     3D projection never samples the room or a white backdrop;
  4. the result is saved in fashion/3d/<id>/ (texture.jpg + meta.json), written only on this machine.
The 3D page projects texture.jpg onto the template garment fitted to the avatar. A single photo can't give real
geometry, so the shape is a fitted template; colours, prints, trim and logos come from the photo itself.
"""
import json
import re
import threading
import time
from pathlib import Path

from core import storage, users

TEMPLATES = ["tee", "longsleeve", "polo", "shirt", "tank", "sweater", "hoodie", "jacket", "blazer", "coat", "zip_hoodie", "vest",
             "jeans", "trousers", "joggers", "shorts", "skirt", "leggings", "dress",
             "sneakers", "hightops", "boots", "dress_shoes", "sandals", "cap", "beanie", "watch", "chain", "glasses"]
SLOT_OF = {**{t: "top" for t in ["tee", "longsleeve", "polo", "shirt", "tank", "sweater", "hoodie", "dress"]},
           **{t: "outer" for t in ["jacket", "blazer", "coat", "zip_hoodie", "vest"]},
           **{t: "bottom" for t in ["jeans", "trousers", "joggers", "shorts", "skirt", "leggings"]},
           **{t: "shoes" for t in ["sneakers", "hightops", "boots", "dress_shoes", "sandals"]},
           "cap": "hat", "beanie": "hat", "watch": "watch", "chain": "neck", "glasses": "eyes"}
CATEGORY_OF = {"top": "top", "outer": "outerwear", "bottom": "bottom", "shoes": "shoes", "hat": "accessory", "watch": "accessory",
               "neck": "accessory", "eyes": "accessory"}
NAMED = {"black": "#17171a", "white": "#f2f1ec", "cream": "#ece3cf", "off-white": "#efeadf", "ivory": "#efe8d6", "beige": "#d6c3a0",
         "tan": "#c39463", "camel": "#b8854f", "brown": "#6b4628", "cognac": "#9a5a2a", "chocolate": "#4a2f20", "khaki": "#bba67a",
         "grey": "#8f9196", "gray": "#8f9196", "charcoal": "#3a3c41", "silver": "#c5c7cb", "navy": "#1f2a44", "blue": "#2f5d9e",
         "light blue": "#a9c6e8", "denim": "#3e5b80", "red": "#b3262e", "burgundy": "#6a1f2b", "maroon": "#5e1e25", "pink": "#e8a3b3",
         "green": "#3f7a4a", "olive": "#6b6d3a", "sage": "#9fae8f", "yellow": "#e3c13c", "mustard": "#c99a2e", "gold": "#c9a253",
         "orange": "#d9772b", "purple": "#6e4a8e", "lavender": "#b9a7d9", "teal": "#2a7b7c"}

_jobs: dict[str, dict] = {}   # item id -> {"state": "...", "detail": "...", "t": time}
_lock = threading.Lock()
_work = threading.Lock()


def item_dir(item_id: str) -> Path:
    from tools import fashion
    d = fashion.fdir() / "3d" / re.sub(r"[^\w-]", "", item_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def meta(item_id: str) -> dict | None:
    from tools import fashion
    p = fashion.fdir() / "3d" / re.sub(r"[^\w-]", "", item_id) / "meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


# -- guesses without a photo ------------------------------------------------------------------------
def guess_template(name: str, category: str = "") -> str:
    """'black and tan chunky high-top sneakers' -> 'hightops'; used until (or instead of) the vision model."""
    n = f"{name} {category}".lower()
    rules = [("tee", r"\btee\b|t-shirt|t shirt"), ("shirt", r"oxford shirt|dress shirt|button[- ]?(down|up)"),
             ("hightops", r"high[- ]?top"), ("boots", r"\bboots?\b|chelsea|timberland"), ("dress_shoes", r"loafer|oxford|derby|dress shoe|heels?\b|pumps?"),
             ("sandals", r"sandal|slide|flip[- ]?flop"), ("sneakers", r"sneaker|trainer|running shoe|\bshoes?\b"),
             ("zip_hoodie", r"zip[- ]?(up )?hoodie"), ("hoodie", r"hoodie|hooded"), ("sweater", r"sweater|jumper|knit\b|cardigan|crewneck|sweatshirt"),
             ("blazer", r"blazer|suit jacket|sport coat"), ("coat", r"\bcoat\b|trench|parka|overcoat"), ("vest", r"\bvest\b|gilet"),
             ("jacket", r"jacket|bomber|overshirt|windbreaker|shacket"), ("polo", r"\bpolo\b"), ("shirt", r"button|oxford shirt|dress shirt|flannel|\bshirt\b"),
             ("tank", r"tank|vest top|camisole|singlet"), ("longsleeve", r"long[- ]?sleeve|henley"), ("tee", r"\btee\b|t-shirt|t shirt"),
             ("joggers", r"jogger|sweatpant|track pant"), ("shorts", r"shorts\b"), ("skirt", r"skirt"), ("leggings", r"legging|tights"),
             ("jeans", r"jeans|denim"), ("trousers", r"trouser|chino|slacks|pants|cargo"), ("dress", r"\bdress\b|gown"),
             ("cap", r"\bcap\b|baseball hat|snapback|\bhat\b"), ("beanie", r"beanie|toque"), ("watch", r"watch"), ("chain", r"chain|necklace|pendant"),
             ("glasses", r"glasses|sunglasses|shades|spectacles")]
    for t, rx in rules:
        if re.search(rx, n):
            return t
    return {"top": "tee", "bottom": "trousers", "outerwear": "jacket", "shoes": "sneakers", "accessory": "watch"}.get(category, "tee")


def colors_from_text(text: str) -> list[str]:
    """'black, tan, mustard' -> hex, in the order given."""
    out = []
    t = (text or "").lower()
    for part in re.split(r"[,/&]| and ", t):
        part = part.strip()
        for name in sorted(NAMED, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", part):
                hx = NAMED[name]
                if hx not in out:
                    out.append(hx)
                break
    return out or ["#7a7f87"]


def basic_model(item: dict) -> dict:
    """What the dressing room wears for an item that has no processed photo (yet)."""
    tpl = guess_template(item.get("name", ""), item.get("category", ""))
    cols = colors_from_text(item.get("colors") or item.get("name", ""))
    m = {"template": tpl, "slot": SLOT_OF[tpl], "colors": cols, "texture": None, "bbox": [0, 0, 1, 1], "status": "basic"}
    if SLOT_OF[tpl] == "shoes":
        m["sole"] = "#f1efe9" if tpl in ("sneakers", "hightops") else cols[-1]
    return m


# -- the vision model -------------------------------------------------------------------------------
SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"}, "template": {"type": "string", "enum": TEMPLATES},
    "colors": {"type": "array", "items": {"type": "string"}}, "pattern": {"type": "string"},
    "details": {"type": "string"}, "worn": {"type": "boolean"}, "view": {"type": "string", "enum": ["front", "back", "side", "top", "pair", "flat"]},
    "toe": {"type": "string", "enum": ["left", "right", "none"]},
    "box_2d": {"type": "array", "items": {"type": "integer"}}},
    "required": ["name", "template", "colors", "pattern", "details", "worn", "view", "toe", "box_2d"]}


def analyze(img, hint: str = "") -> dict:
    """Identify one clothing item in a photo with the local vision model."""
    import base64
    import io

    from core import engeyes
    im = img.copy(); im.thumbnail((1024, 1024))
    buf = io.BytesIO(); im.convert("RGB").save(buf, format="JPEG", quality=88)
    prompt = ("You catalogue clothing for a wardrobe app. Identify the ONE main clothing item or accessory in this photo"
              + (f" (the owner says: {hint})" if hint else "") + ". It may be worn, on a bed, hanging, or on its own. "
              "name: short shopping-style name with colour, e.g. 'black crew-neck tee with tan trim'. "
              "template: the closest 3D template. colors: 1-4 plain colour words, main colour first. "
              "pattern: solid, striped, graphic print, logo, plaid, colour-block, camo or textured. details: visible design, "
              "logos, trim, text. worn: is a person wearing it. view: which side faces the camera. toe: for a single shoe seen "
              "from the side, which way its toe points, else none. box_2d: [x0,y0,x1,y1] of the item only, 0-1000.")
    return engeyes._vlm([base64.b64encode(buf.getvalue()).decode()], prompt, SCHEMA)


# -- the texture -------------------------------------------------------------------------------------
def prepare_texture(path: Path, box: list | None = None, white_bg: bool = False):
    """Cut the garment out and fill everything else with its own colours. Returns (PIL image, bbox 0-1, palette hex, mask)."""
    import cv2
    import numpy as np
    from PIL import Image

    im = np.asarray(Image.open(path).convert("RGB"))
    h, w = im.shape[:2]
    scale = 1024 / max(h, w)
    if scale < 1:
        im = cv2.resize(im, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = im.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in ((box[0] * w, box[1] * h, box[2] * w, box[3] * h) if box else (0, 0, w, h))]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, max(x0 + 8, x1)), min(h, max(y0 + 8, y1))
    if white_bg:
        # a product shot: near-white connected to the border is backdrop (stricter for white garments)
        def backdrop(level):
            flood = (im.min(axis=2) > level).astype(np.uint8); ff = np.zeros((h + 2, w + 2), np.uint8)
            for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (0, h // 2), (w - 1, h // 2)]:
                if flood[sy, sx] == 1:
                    cv2.floodFill(flood, ff, (sx, sy), 2)
            return (flood != 2).astype(np.uint8)
        mask = backdrop(236)
        area = max(1, (x1 - x0) * (y1 - y0))
        if mask[y0:y1, x0:x1].sum() < area * .22:
            mask = backdrop(247)
        # the invisible mannequin (white/grey, unsaturated) is not part of a coloured garment
        hsv = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)
        sel = mask.astype(bool)
        garment_v = np.median(hsv[..., 2][sel]) if sel.any() else 255
        garment_s = np.median(hsv[..., 1][sel]) if sel.any() else 0
        if garment_v > 200 and garment_s < 30:
            # a white garment on a white backdrop can't be cut out reliably: use the whole (white) product frame
            mask = np.zeros((h, w), np.uint8); mask[y0:y1, x0:x1] = 1
        elif garment_v < 175 or garment_s > 55:
            pale = ((hsv[..., 2] > 178) & (hsv[..., 1] < 34)).astype(np.uint8)
            mask = mask * (1 - pale)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    else:
        mask = np.zeros((h, w), np.uint8)
        rect = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(im[:, :, ::-1].copy(), mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
            mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)
        except cv2.error:
            mask[y0:y1, x0:x1] = 1
    keep = np.zeros_like(mask); keep[y0:y1, x0:x1] = 1      # the vision model's box: no mannequin neck, stand or person
    mask = mask * keep
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n > 1:
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = (lab == biggest).astype(np.uint8)
    if mask.sum() < 400:
        mask[y0:y1, x0:x1] = 1
    ys, xs = np.where(mask)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max(), ys.max()
    # palette: k-means on garment pixels
    px = im[mask.astype(bool)].reshape(-1, 3).astype(np.float32)
    if len(px) > 20000:
        px = px[np.random.default_rng(0).choice(len(px), 20000, replace=False)]
    k = 4
    _, labels, centers = cv2.kmeans(px, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    order = np.argsort(-counts)
    palette = ["#%02x%02x%02x" % tuple(int(c) for c in centers[i]) for i in order if counts[i] > len(px) * .04]
    # fill the background with the garment's own colours (pull-push), so stray texture samples land on fabric
    fill = im.astype(np.float32).copy(); m = mask.astype(np.float32)
    acc, wacc = fill * m[..., None], m.copy()
    pyr = [(acc, wacc)]
    a, ww = acc, wacc
    while min(a.shape[:2]) > 4:
        a, ww = cv2.pyrDown(a), cv2.pyrDown(ww)
        pyr.append((a, ww))
    up_a, up_w = pyr[-1]
    for a, ww in reversed(pyr[:-1]):
        up_a = cv2.resize(up_a, (a.shape[1], a.shape[0])); up_w = cv2.resize(up_w, (ww.shape[1], ww.shape[0]))
        blend = np.clip(ww * 4, 0, 1)[..., None]
        up_a = a * blend + up_a * (1 - blend); up_w = ww * blend[..., 0] + up_w * (1 - blend[..., 0])
    filled = up_a / np.maximum(up_w[..., None], 1e-4)
    core = cv2.erode(mask, np.ones((5, 5), np.uint8))           # drop the cut-out's edge halo
    soft = cv2.GaussianBlur(core.astype(np.float32), (0, 0), 1.2)[..., None]
    out = np.clip(im * soft + filled * (1 - soft), 0, 255).astype(np.uint8)
    bbox = [round(bx0 / w, 4), round(by0 / h, 4), round(bx1 / w, 4), round(by1 / h, 4)]
    return Image.fromarray(out), bbox, palette, mask


def sole_color(path: Path, bbox: list, mask=None) -> str | None:
    """The bottom strip of a shoe (its sole), for sneakers and boots."""
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGB").resize((mask.shape[1], mask.shape[0]))) if mask is not None else None
    if im is None:
        return None
    ys, xs = np.where(mask)
    if not len(ys):
        return None
    cut = ys > ys.min() + (ys.max() - ys.min()) * .86
    if cut.sum() < 30:
        return None
    c = np.median(im[ys[cut], xs[cut]], axis=0)
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


# -- the pipeline -----------------------------------------------------------------------------------
def process(item_id: str, owner: str | None = None, force: bool = False, correction: dict | None = None) -> dict:
    """Turn one wardrobe item's photo into a wearable garment. Runs on the PC (vision model + GPU)."""
    from tools import fashion, studio
    uid = owner or users.current()
    with users.acting_as(uid):
        items = {i["id"]: i for i in fashion._load("wardrobe.json", [])}
        item = items.get(item_id)
        if not item:
            raise ValueError("No such wardrobe item.")
        old = meta(item_id) or {}
        if old.get("status") == "ready" and not force and not correction:
            return old
        _set_job(item_id, "working", "Looking at the photo…")
        photo = fashion.fdir() / item["photo"] if item.get("photo") else None
        if not photo or not photo.exists():
            m = {**basic_model(item), **(correction or {})}
            m["slot"] = SLOT_OF.get(m["template"], m.get("slot"))
            _write_meta(item_id, m)
            _set_job(item_id, "done", "No photo: using its colours on the template.")
            return m
        from PIL import Image
        guessed = False
        try:
            info = analyze(Image.open(photo), item.get("name", ""))
        except Exception as e:
            print(f"[garment3d] vision unavailable: {e}")
            guessed = not (correction or {}).get("template")
            info = {"template": guess_template(item.get("name", ""), item.get("category", "")), "colors": [], "pattern": "solid",
                    "details": "", "worn": False, "view": "front", "toe": "none", "box_2d": [0, 0, 1000, 1000], "name": item.get("name", "")}
        if correction and correction.get("template"):
            info["template"] = correction["template"]
        clean = photo.with_name(f"{photo.stem}-clothes.jpg")
        source, white = photo, False
        if clean.exists():
            source, white = clean, True
        elif info.get("worn") and SLOT_OF.get(info["template"]) in ("top", "outer", "bottom"):
            _set_job(item_id, "working", "Making a clean product shot of it (GPU, about a minute)…")
            try:
                done = studio.clean_shot(str(photo), item.get("description") or info.get("name") or item.get("name", ""))
                if done and Path(done).exists():
                    source, white = Path(done), True
            except Exception as e:
                print(f"[garment3d] clean shot failed, using the photo: {e}")
        _set_job(item_id, "working", "Cutting it out and mapping it onto the 3D garment…")
        box = None
        if not white:
            b = info.get("box_2d") or [0, 0, 1000, 1000]
            if len(b) == 4 and b[2] > b[0] and b[3] > b[1]:
                box = [max(0, b[0] / 1000), max(0, b[1] / 1000), min(1, b[2] / 1000), min(1, b[3] / 1000)]
        else:
            box = _product_box(source, info.get("template", "tee"))
        tex, bbox, palette, mask = prepare_texture(source, box, white_bg=white)
        d = item_dir(item_id)
        tex.save(d / "texture.jpg", quality=90)
        named = colors_from_text(", ".join(info.get("colors") or [])) if info.get("colors") else []
        colors = (palette[:3] or named or colors_from_text(item.get("colors", "")))
        tpl = (correction or {}).get("template") or info.get("template") or guess_template(item.get("name", ""), item.get("category", ""))
        m = {"template": tpl, "slot": SLOT_OF.get(tpl, "top"), "colors": (correction or {}).get("colors") or colors,
             "texture": "texture.jpg", "bbox": bbox, "status": "ready", "pattern": info.get("pattern", ""), "details": info.get("details", ""),
             "view": info.get("view", "front"), "toe": info.get("toe") if info.get("toe") in ("left", "right") else "left",
             "source": source.name, "processed": time.strftime("%Y-%m-%d %H:%M")}
        if m["slot"] == "shoes":
            m["sole"] = sole_color(source, bbox, mask) or "#f1efe9"
        if m["slot"] in ("watch", "neck", "eyes"):
            m["finish"] = "gold" if any(c in (info.get("colors") or []) for c in ("gold", "yellow")) else \
                "silver" if any(c in (info.get("colors") or []) for c in ("silver", "grey", "gray", "steel")) else ""
        if guessed:
            m["guessed"] = True   # the vision model was down: the page asks the person to confirm the type
        _write_meta(item_id, m)
        if item.get("auto_name") and info.get("name") and not guessed:   # added from a photo with no name: use what the vision model saw
            rows = fashion._load("wardrobe.json", [])
            for r in rows:
                if r["id"] == item_id:
                    r.update(name=info["name"][:80], category=CATEGORY_OF[m["slot"]], colors=", ".join(info.get("colors") or [])[:80],
                             description=(info.get("details") or r.get("description", ""))[:300])
                    r.pop("auto_name", None)
            fashion._save("wardrobe.json", rows)
        try:
            from tools import fitting
            fitting.catalogue(item_id, tpl)      # the clean cut-out for the closet grid, and the season tag
        except Exception as e:  # noqa: BLE001
            print(f"[garment3d] catalogue image skipped: {e}")
        _set_job(item_id, "done", "Ready to wear." if not guessed else
                 "Ready to wear. I couldn't recognise it automatically (the local vision model is down), so check its type with ⋯.")
        return m


def _product_box(path: Path, template: str) -> list | None:
    """On a FLUX product shot the garment sits on an invisible mannequin: trim the neck and stand off the top/bottom."""
    slot = SLOT_OF.get(template, "top")
    if slot in ("top", "outer"):
        return [0, .14, 1, .97]
    if slot == "bottom":
        return [0, .16, 1, 1]
    return None


def _write_meta(item_id: str, m: dict) -> None:
    (item_dir(item_id) / "meta.json").write_text(json.dumps(m, indent=1), encoding="utf-8")


def _set_job(item_id: str, state: str, detail: str) -> None:
    with _lock:
        _jobs[item_id] = {"state": state, "detail": detail, "t": time.time()}


def job(item_id: str) -> dict | None:
    return _jobs.get(item_id)


def process_async(item_id: str, owner: str, force: bool = False, correction: dict | None = None) -> None:
    def run():
        try:
            with _work:   # one at a time: the vision model and the GPU clean shot don't share well
                process(item_id, owner, force, correction)
        except Exception as e:
            print(f"[garment3d] {item_id}: {type(e).__name__}: {e}")
            _set_job(item_id, "error", f"Couldn't process it: {str(e)[:160]}")
    _set_job(item_id, "working", "Queued…")
    threading.Thread(target=run, daemon=True, name=f"garment-{item_id}").start()
