"""The Wardrobe page's photoreal side (UI: app/wardrobe.html).

The hero is the PERSON: their own full-body photo, dressed in photos of clothes they own by the local
try-on pipeline (tools/studio.py, FLUX.2 klein on the PC's GPU). This module holds what that page needs
on top of the closet (tools/dressing.py) and the wardrobe store (tools/fashion.py):

  the render queue   one try-on at a time, honest states (queued, rendering, done, failed with the reason),
                     and it never starts while a game is running: it waits and says so.
  fits               each finished try-on, kept per outfit (fits/<id>.png + fits-<host>.json, merged on read
                     like the saved looks, so the two machines never fight over one file).
  catalogue images   a clean cut-out of each garment photo (rembg, only when its model is already on disk).
  tags               season from the garment's type; category and colour come from tools/garment3d.py.
  the week plan      saved looks assigned to days, in the same calendar.json the voice tools read.

Everything here runs as the person it is for: call inside users.acting_as(person).
"""
import re
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from core import users
from core.words import plural

PEOPLE = ("owner", "maya")            # the two closets on the page; the same ids tools/studio.py dresses
MAX_PIECES = 3                           # what the try-on pipeline takes in one render
MAX_QUEUE = 6
RETRY_S = 30                             # a busy GPU is asked again this often
MAX_TRIES = 20
ORDER = ["top", "bottom", "outer", "shoes", "hat", "neck", "watch", "eyes"]   # which pieces a render takes first
SEASONS = ("all", "warm", "cold")
COLD = {"coat", "sweater", "hoodie", "zip_hoodie", "jacket", "beanie", "boots", "scarf", "longsleeve"}
WARM = {"tank", "shorts", "sandals", "slides", "tee", "polo", "skirt"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")

_lock = threading.Lock()
_jobs: list = []                         # this process's try-on jobs, oldest first (finished ones are kept for a while)
_worker = {"thread": None}
_now = time.time                         # tests pin these
_today = date.today


def _fd() -> Path:
    from tools import fashion
    return fashion.fdir()


def _load(name, default):
    from tools import fashion
    return fashion._load(name, default)


def _save(name, value):
    from tools import fashion
    fashion._save(name, value)


def _host() -> str:
    from core import pcbridge
    return "pc" if pcbridge.local_pc() else "server"


# -- the person's own photo ---------------------------------------------------------------------------
def photo() -> str | None:
    """The full-body photo the try-on dresses (the newest one they uploaded), as a path inside fashion/."""
    from tools import fashion
    f = fashion._avatar_file(False)
    return f"avatar/{f.name}" if f else None


# -- catalogue images and tags ------------------------------------------------------------------------
def season_of(template: str, text: str = "") -> str:
    """Warm-weather, cold-weather or all-year, from the kind of garment (and a few plain words)."""
    t = (text or "").lower()
    if template in COLD or any(w in t for w in ("wool", "fleece", "puffer", "thermal", "winter", "knit")):
        return "cold"
    if template in WARM or any(w in t for w in ("linen", "summer", "sleeveless", "short sleeve")):
        return "warm"
    return "all"


def cutout_ready() -> bool:
    """The background-removal model is already on this machine (nothing is ever downloaded here)."""
    try:
        import rembg  # noqa: F401
    except Exception:
        return False
    home = Path(os.environ.get("U2NET_HOME") or Path.home() / ".u2net")
    return (home / "u2net.onnx").exists()


_REMBG = {"session": None}


def _remove_background(img):
    """RGBA cut-out of the main object, on the CPU (the graphics card is left alone)."""
    from rembg import new_session, remove
    if _REMBG["session"] is None:
        _REMBG["session"] = new_session("u2net", providers=["CPUExecutionProvider"])
    return remove(img, session=_REMBG["session"], post_process_mask=True)


def make_cutout(item_id: str) -> str | None:
    """The clean catalogue image of a closet item: the garment alone on a transparent background, trimmed.
    Uses the clothes-only product shot when one was made from a worn photo. Returns its path inside fashion/,
    or None when the photo is kept as it is (no photo, no model on disk, or nothing sensible came out)."""
    from PIL import Image, ImageOps
    item = next((i for i in _load("wardrobe.json", []) if i.get("id") == item_id), None)
    if not item or not item.get("photo") or not cutout_ready():
        return None
    src = _fd() / item["photo"]
    clean = src.with_name(f"{src.stem}-clothes.jpg")
    src = clean if clean.exists() else src
    if not src.exists():
        return None
    try:
        img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
        img.thumbnail((1100, 1100))
        cut = _remove_background(img).convert("RGBA")
        alpha = cut.getchannel("A").point(lambda a: 255 if a > 24 else 0)
        box = alpha.getbbox()
        if not box:
            return None
        covered = sum(alpha.histogram()[255:]) / float(alpha.width * alpha.height)
        if covered < .04 or covered > .97:      # nothing found, or nothing removed: the photo is the better picture
            return None
        pad = round(.04 * max(cut.size))
        cut = cut.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(cut.width, box[2] + pad), min(cut.height, box[3] + pad)))
        cut.thumbnail((900, 900))
        (_fd() / "cutouts").mkdir(parents=True, exist_ok=True)
        cut.save(_fd() / "cutouts" / f"{item_id}.png")
        return f"cutouts/{item_id}.png"
    except Exception as e:  # noqa: BLE001
        print(f"[fitting] cut-out of {item_id} failed, keeping the photo: {type(e).__name__}: {e}")
        return None


def catalogue(item_id: str, template: str = "") -> dict:
    """After a garment is digitised: make its clean catalogue image and fill the season tag if nobody set one."""
    made = make_cutout(item_id)
    rows = _load("wardrobe.json", [])
    for r in rows:
        if r.get("id") == item_id and not r.get("season"):
            r["season"] = season_of(template, f"{r.get('name', '')} {r.get('description', '')}")
            r["season_auto"] = True
            _save("wardrobe.json", rows)
    return {"cutout": made}


def _cutout_of(item_id: str) -> str | None:
    p = _fd() / "cutouts" / f"{item_id}.png"
    return f"cutouts/{item_id}.png" if p.exists() else None


# -- "prepare my closet": clean catalogue images for every piece that has a photo ---------------------------
_prep: dict = {}                         # person -> {"state", "done", "total", "detail", "made", "needs_photo", "waiting"}


def _clothes_shot(item: dict) -> Path | None:
    """The clothes-only product shot made from a worn photo (next to the photo), if it exists."""
    if not item.get("photo"):
        return None
    src = _fd() / item["photo"]
    clean = src.with_name(f"{src.stem}-clothes.jpg")
    return clean if clean.exists() else None


def prepare_status(person: str) -> dict:
    with _lock:
        return dict(_prep.get(person) or {"state": "idle", "done": 0, "total": 0, "detail": "", "made": [], "needs_photo": [],
                                          "waiting": []})


def prepare_closet(person: str = "") -> dict:
    """Start making the clean catalogue images of the person's closet in the background: the garment alone on a
    transparent background (rembg on the CPU, only when its model is on disk), from the clothes-only product shot
    when there is one. A piece whose product shot is missing needs the graphics card (FLUX), so it waits while a
    game runs or a try-on is rendering. Pieces without a photo are listed: nothing can be made from nothing."""
    person = person or users.current()
    with _lock:
        if _prep.get(person, {}).get("state") == "working":
            return dict(_prep[person])
    if not cutout_ready():
        raise ValueError("The background remover isn't installed on this PC (its model file is missing), so no clean cut-outs can be made.")
    rows = _load("wardrobe.json", [])
    todo = [r for r in rows if r.get("photo") and not _cutout_of(r["id"])]
    need_photo = [r["id"] for r in rows if not r.get("photo")]
    with _lock:
        _prep[person] = {"state": "working" if todo else "done", "done": 0, "total": len(todo), "made": [], "waiting": [],
                         "needs_photo": need_photo,
                         "detail": "Starting." if todo else "Every piece with a photo already has its cut-out."}
        out = dict(_prep[person])
    if todo:
        threading.Thread(target=_prepare_run, args=(person, [r["id"] for r in todo]), daemon=True, name="closet-prepare").start()
    return out


def _prepare_run(person: str, ids: list) -> None:
    from tools import studio
    st = _prep[person]
    try:
        with users.acting_as(person):
            rows = {r["id"]: r for r in _load("wardrobe.json", [])}
            missing = [i for i in ids if _clothes_shot(rows[i]) is None]
            if missing:
                busy = game_running() or any(j["state"] == "rendering" for j in _jobs)
                if busy:
                    st["waiting"] = missing         # the graphics card is not ours right now: cut these out later
                else:
                    with _lock:
                        st["detail"] = "Making clean product photos of your clothes on the graphics card."
                    try:
                        studio.prepare_clothes(person, missing, progress=lambda t: st.update(detail=t[:80]))
                    except Exception as e:  # noqa: BLE001
                        st["waiting"] = missing
                        print(f"[fitting] clothes-only photos failed: {type(e).__name__}: {e}")
            for i in ids:
                if i in st["waiting"] and _clothes_shot(rows[i]) is None:
                    st["done"] += 1
                    continue
                with _lock:
                    st["detail"] = f"Cutting out {rows[i].get('name', i)}."
                if make_cutout(i):
                    st["made"].append(i)
                st["done"] += 1
        with _lock:
            left = len(st["waiting"])
            st.update(state="done", detail=f"Made {len(st['made'])} cut-out{'s' if len(st['made']) != 1 else ''}."
                      + (f" {left} more wait for the graphics card." if left else ""))
    except Exception as e:  # noqa: BLE001
        with _lock:
            st.update(state="failed", detail=f"Preparing the closet stopped: {str(e)[:160]}")


# -- what the page shows -------------------------------------------------------------------------------
def garments() -> list:
    """The person's closet for the page: real photos first, the clean cut-out when there is one, and the tags."""
    from tools import dressing
    seasons = {i.get("id"): i for i in dressing._wardrobe()}
    out = []
    for g in dressing.closet():
        m = g.get("model") or {}
        raw = seasons.get(g["id"], {})
        photo_rel = g["photo"][len("fashion/"):] if g.get("photo") else None
        thumb_rel = g["thumb"][len("fashion/"):] if g.get("thumb") else None
        out.append({"id": g["id"], "name": g["name"] or "Unnamed", "category": g.get("category") or "", "slot": m.get("slot") or "top",
                    "template": m.get("template") or "", "colors": [c for c in (m.get("colors") or []) if isinstance(c, str)][:3],
                    "colors_text": g.get("colors_text", ""), "description": g.get("description", ""),
                    "season": raw.get("season") if raw.get("season") in SEASONS else season_of(m.get("template") or "", g["name"]),
                    "favorite": g["favorite"], "added": g.get("added", ""),
                    "photo": photo_rel, "image": _cutout_of(g["id"]) or thumb_rel or photo_rel, "cutout": bool(_cutout_of(g["id"])),
                    "guessed": bool(m.get("guessed")), "pending": bool(raw.get("pending")), "job": g.get("job")})
    return out


def outfit_key(ids) -> str:
    return "+".join(sorted({str(i) for i in ids}))


def fits() -> list:
    """Every finished try-on, newest first (both machines' lists, minus the deleted ones)."""
    rows, gone = [], set()
    for h in ("pc", "server"):
        d = _load(f"fits-{h}.json", {})
        rows += [r for r in d.get("fits", []) if isinstance(r, dict) and r.get("id")]
        gone |= set(d.get("deleted", []))
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r.get("created", ""), reverse=True):
        if r["id"] in gone or r["id"] in seen or not (_fd() / "fits" / f"{r['id']}.png").exists():
            continue
        seen.add(r["id"])
        out.append({**r, "image": f"fits/{r['id']}.png"})
    return out


def save_fit(data: bytes, ids: list, rendered: list, seconds: float, fit_id: str = "") -> dict:
    fid = fit_id or uuid.uuid4().hex[:10]
    (_fd() / "fits").mkdir(parents=True, exist_ok=True)
    (_fd() / "fits" / f"{fid}.png").write_bytes(data)
    row = {"id": fid, "key": outfit_key(ids), "items": sorted({str(i) for i in ids}), "rendered": list(rendered),
           "seconds": round(float(seconds), 1), "created": datetime.fromtimestamp(_now()).isoformat(timespec="seconds")}
    d = _load(f"fits-{_host()}.json", {})
    d = {"fits": ([r for r in d.get("fits", []) if r.get("id") != fid] + [row])[-200:], "deleted": d.get("deleted", [])}
    _save(f"fits-{_host()}.json", d)
    return {**row, "image": f"fits/{fid}.png"}


def delete_fit(fit_id: str) -> bool:
    fid = "".join(c for c in str(fit_id) if c.isalnum())[:16]
    if not fid:
        return False
    d = _load(f"fits-{_host()}.json", {})
    d = {"fits": [r for r in d.get("fits", []) if r.get("id") != fid], "deleted": list(set(d.get("deleted", [])) | {fid})[-500:]}
    _save(f"fits-{_host()}.json", d)
    (_fd() / "fits" / f"{fid}.png").unlink(missing_ok=True)
    return True


def look_ids(look: dict) -> list:
    """The closet garments in a saved look."""
    return sorted({str(r.get("id")) for r in (look.get("items") or {}).values() if isinstance(r, dict) and r.get("src") == "closet" and r.get("id")})


def looks() -> list:
    """Saved looks made of the person's own clothes, each with its newest try-on picture."""
    from tools import dressing
    have = {g["id"] for g in dressing._wardrobe()}
    newest = {}
    for f in fits():
        newest.setdefault(f["key"], f)
    out = []
    for lk in dressing.looks():
        if not isinstance(lk, dict) or not lk.get("id"):
            continue
        ids = [i for i in look_ids(lk) if i in have]
        if not ids:
            continue                      # an avatar-only look of stand-ins belongs to the 3D room
        fit = newest.get(outfit_key(ids))
        out.append({"id": lk["id"], "name": lk.get("name") or "My look", "occasion": lk.get("occasion", ""), "favorite": bool(lk.get("favorite")),
                    "items": ids, "key": outfit_key(ids), "image": fit["image"] if fit else None, "updated": lk.get("updated", "")})
    return out


def save_look(name: str, ids: list, occasion: str = "", look_id: str = "", favorite: bool = False) -> dict:
    """Save the composed outfit as a look the 3D room and the voice tools also see."""
    from tools import dressing
    by_id = {g["id"]: g for g in garments()}
    items = {}
    for i in sorted((str(x) for x in ids if str(x) in by_id), key=lambda x: _rank(by_id[x]["slot"])):
        items.setdefault(by_id[i]["slot"], {"src": "closet", "id": i})
    if not items:
        raise ValueError("Pick at least one piece from the closet first.")
    return dressing.save_look(str(name).strip()[:60], items, str(occasion)[:40], "", bool(favorite), str(look_id)[:12])


def _rank(slot: str) -> int:
    return ORDER.index(slot) if slot in ORDER else len(ORDER)


# -- the week plan -------------------------------------------------------------------------------------
def week() -> list:
    """The next seven days with the look planned for each (the same calendar the voice tools read)."""
    cal = _load("calendar.json", {})
    names = {lk["id"]: lk for lk in looks()}
    out = []
    for n in range(7):
        d = _today() + timedelta(days=n)
        row = cal.get(d.isoformat()) if isinstance(cal.get(d.isoformat()), dict) else {}
        lk = names.get(row.get("look_id") or "")
        out.append({"date": d.isoformat(), "weekday": d.strftime("%a"), "day": d.day, "today": n == 0,
                    "look_id": lk["id"] if lk else "", "name": lk["name"] if lk else (row.get("description") or ""),
                    "image": lk["image"] if lk else None, "occasion": row.get("occasion", "")})
    return out


def plan(day: str, look_id: str = "") -> list:
    """Put a saved look on a day, or clear the day with an empty look."""
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        raise ValueError("Pick a day.") from None
    cal = _load("calendar.json", {})
    if not look_id:
        cal.pop(d.isoformat(), None)
    else:
        lk = next((x for x in looks() if x["id"] == look_id), None)
        if not lk:
            raise ValueError("That look isn't saved any more.")
        cal[d.isoformat()] = {"description": lk["name"], "item_ids": lk["items"], "occasion": lk.get("occasion", ""), "look_id": lk["id"]}
    _save("calendar.json", cal)
    return week()


# -- the stylist ---------------------------------------------------------------------------------------
_COLD = re.compile(r"\b(cold|chilly|freez(?:e|ing)|frozen|frosty|winter|wintry|rain(?:y|ing|s)?|wet|wind(?:y)?|snow(?:y|ing|s)?)\b")
_HOT = re.compile(r"\b(heat|heatwave|summer|humid|sunny)\b")
# "warm" and "hot" are weather only when said about the weather: "something warm" asks for the opposite, "hot pink" is a colour
_WARM = re.compile(r"\b(?:warm|hot) (?:out|outside|outdoors|weather|day|days|today|tonight|tomorrow|evening|night|afternoon|morning)\b"
                   r"|\b(?:it'?s|it is|it will be|it'll be|is|be|getting|gets|turned) (?:(?:really|very|so|too|quite|pretty|rather) )?(?:warm|hot)(?! pink)\b")


def weather_words(weather: str) -> str:
    """Plain words the deterministic stylist already understands, for the weather picked on the page or spoken.
    Whole words only: "training" is not rain, "window" is not wind, "photoshoot" is not hot."""
    w = " ".join((weather or "").lower().replace("’", "'").split())
    if _COLD.search(w):
        return " with a jacket"
    if w in ("warm", "hot") or _HOT.search(w) or _WARM.search(w):
        return " without a jacket"
    return ""


def stylist(occasion: str, weather: str = "", n: int = 3) -> dict:
    """What to wear: outfits scored from the person's own closet (tools/dressing.py), best first."""
    from tools import dressing
    request = (str(occasion).strip() or "everyday")[:200] + weather_words(weather)
    by_id = {g["id"]: g for g in garments()}
    picks = []
    for lk in dressing.suggest(request, n=max(1, min(5, n))):
        ids = [r["id"] for r in lk["items"].values() if r.get("src") == "closet" and r.get("id") in by_id]
        if not ids:
            continue
        ids.sort(key=lambda i: _rank(by_id[i]["slot"]))
        stand_ins = sum(r.get("src") != "closet" for r in lk["items"].values())
        picks.append({"name": lk["name"], "occasion": lk["occasion"], "items": ids, "why": lk["why"], "stand_ins": stand_ins,
                      "_items": lk["items"], "_target": lk.get("_target")})
    if picks:
        top = picks[0]
        dressing.set_current(top["_items"], top["name"], top["why"], by="stylist", target=top["_target"])
    for p in picks:
        p.pop("_items", None); p.pop("_target", None)
    message = "" if picks else ("Your closet is empty. Add a few pieces with a photo and I can style them." if not by_id
                                else "I couldn't build an outfit from these pieces yet. Add a top, a bottom and shoes.")
    return {"request": request, "picks": picks, "message": message}


def picked() -> dict | None:
    """The stylist's latest answer (also what a voice request chose), for the page to show."""
    from tools import dressing
    cur = dressing.current()
    if cur.get("by") != "stylist":
        return None
    have = {g["id"] for g in dressing._wardrobe()}
    ids = [i for i in look_ids(cur) if i in have]
    return {"name": cur.get("name", ""), "why": cur.get("why", ""), "items": ids, "t": cur.get("t", 0)} if ids else None


# -- the render queue -----------------------------------------------------------------------------------
def game_running() -> str:
    """The game holding the PC's graphics card right now, or ''. Only the PC can know; the laptop asks by trying."""
    from core import pcbridge
    if not pcbridge.local_pc():
        return ""
    from tools import studio
    return studio.game_running()


def choose_pieces(ids: list) -> tuple[list, list]:
    """Which pieces a render takes (at most three, clothes before accessories) and which are left out."""
    by_id = {g["id"]: g for g in garments()}
    known = [i for i in dict.fromkeys(str(x) for x in ids) if i in by_id]
    usable = sorted((i for i in known if by_id[i]["photo"]), key=lambda i: _rank(by_id[i]["slot"]))
    return usable[:MAX_PIECES], [i for i in known if i not in usable[:MAX_PIECES]]


def left_out_reasons(ids: list, rendered: list) -> dict:
    """Why each piece the render does not use was left out, in words for the page."""
    by_id = {g["id"]: g for g in garments()}
    out = {}
    for i in dict.fromkeys(str(x) for x in ids):
        if i in rendered or i not in by_id:
            continue
        out[i] = ("It has no photo yet, and the try-on dresses you from photos. Add one on the piece's page."
                  if not by_id[i]["photo"] else f"A try-on takes at most {plural(MAX_PIECES, 'piece')}, clothes before accessories.")
    return out


# How far along a render is, for the bar: the stages the child reports (tools/studio.py, imaging/engine.py).
_STAGES = (("Making room", .03), ("Loading the image model", .08), ("Preparing the clothes photo", .14),
           ("Reading the request", .30), ("Moving the image model", .34), ("Putting your real face", .96))


def progress_of(text: str) -> float:
    """0..1 from a progress line; -1 when it says nothing new."""
    m = re.match(r"Drawing, step (\d+) of (\d+)", text)
    if m:
        return .36 + .58 * min(1, int(m.group(1)) / max(1, int(m.group(2))))
    for words, frac in _STAGES:
        if text.startswith(words):
            return frac
    return -1.0


def _progress(job: dict, text: str) -> None:
    frac = progress_of(text)
    with _lock:
        if job["state"] != "rendering":
            return
        job["phase"] = text[:80]
        if frac >= 0:
            job["progress"] = max(job.get("progress", 0), frac)


def _public(j: dict) -> dict:
    now = _now()
    out = {k: j[k] for k in ("id", "person", "items", "rendered", "left_out", "state", "detail", "fit")}
    out["left_reasons"] = j.get("left_reasons", {})
    out["progress"] = round(j.get("progress", 0.0), 2)
    out["phase"] = j.get("phase", "") if j["state"] == "rendering" else ""
    out["waited"] = int(max(0, (j["started"] or j["finished"] or now) - j["created"]))
    out["elapsed"] = int(max(0, (j["finished"] or now) - j["started"])) if j["started"] else 0
    out["position"] = 0
    return out


def jobs(person: str) -> list:
    """The person's try-on jobs: waiting and running ones, plus what finished in the last ten minutes."""
    with _lock:
        now = _now()
        _jobs[:] = [j for j in _jobs if j["state"] in ("queued", "rendering") or now - (j["finished"] or now) < 3600]
        queue = [j["id"] for j in _jobs if j["state"] == "queued"]
        out = []
        for j in _jobs:
            if j["person"] != person or (j["state"] in ("done", "failed") and now - j["finished"] > 600):
                continue
            row = _public(j)
            row["position"] = queue.index(j["id"]) + 1 if j["id"] in queue else 0
            out.append(row)
        return out


def enqueue(ids: list, notes: str = "", by: str = "") -> dict:
    """Ask for a try-on of these closet pieces on the current person. Raises ValueError with words for the page."""
    person = users.current()
    if not photo():
        raise ValueError("Add a full-body photo first: the try-on dresses the real you.")
    rendered, left = choose_pieces(ids)
    if not rendered:
        raise ValueError("Pick at least one piece that has a photo.")
    key = outfit_key(ids)
    game = game_running()
    with _lock:
        for j in _jobs:
            if j["person"] == person and j["key"] == key and j["state"] in ("queued", "rendering"):
                return _public(j)
        if sum(j["state"] == "queued" for j in _jobs) >= MAX_QUEUE:
            raise ValueError("The try-on queue is full. Let one finish first.")
        j = {"id": uuid.uuid4().hex[:10], "person": person, "by": by or person, "items": sorted({str(i) for i in ids}), "key": key,
             "rendered": rendered, "left_out": left, "left_reasons": left_out_reasons(ids, rendered), "notes": str(notes)[:200], "state": "queued",
             "detail": _waiting_words(game) if game else "Queued.", "created": _now(), "started": 0, "finished": 0, "fit": None,
             "tries": 0, "retry_at": 0}
        _jobs.append(j)
        out = _public(j)
    _ensure_worker()
    return out


def cancel(job_id: str, person: str) -> bool:
    """Take a waiting job out of the queue (a render that already started runs to its end)."""
    with _lock:
        for j in _jobs:
            if j["id"] == job_id and j["person"] == person and j["state"] == "queued":
                j.update(state="failed", detail="Cancelled.", finished=_now())
                return True
    return False


def _waiting_words(game: str) -> str:
    return f"Waiting: {game} is running, and a game keeps the graphics card. It starts when the game closes."


def _render(job: dict) -> bytes:
    """Render one job for real: on the PC through the local pipeline, on the laptop by asking the PC.
    Returns the PNG. Raises RuntimeError('... busy ...') when the graphics card is taken."""
    from core import pcbridge
    from tools import studio
    if pcbridge.local_pc():
        path = studio.render_wardrobe_tryon(job["person"], job["person"], job["rendered"], job["notes"],
                                            progress=lambda text: _progress(job, text))
        if not path:
            raise ValueError("There is no full-body photo for that person yet.")
        return Path(path).read_bytes()
    try:
        with users.acting_as("owner"):     # the laptop's link to the PC is the owner's; the person rides along
            data, _ = pcbridge.request("/api/pc/avatar/tryon", "POST", {"person": job["person"], "garment_owner": job["person"],
                                                                         "garment_ids": job["rendered"], "notes": job["notes"]})
        return data
    except pcbridge.PCError as e:
        if e.status == 503 and "busy" in str(e).lower():
            raise RuntimeError(str(e)) from None
        if e.status in (502, 503, 504):
            raise ValueError("Your PC does the rendering and it isn't reachable right now. Wake it and try again.") from None
        raise ValueError(str(e)) from None


def step() -> dict | None:
    """Move the queue one step: start the oldest waiting job unless a game is running or something is rendering.
    Returns the job it finished (done or failed), or None. The worker thread calls this; tests call it directly."""
    with _lock:
        if any(j["state"] == "rendering" for j in _jobs):
            return None
        job = next((j for j in _jobs if j["state"] == "queued"), None)
        if not job:
            return None
    game = game_running()
    with _lock:
        if job["state"] != "queued":
            return None
        if game:
            job["detail"] = _waiting_words(game)
            return None
        if _now() < job["retry_at"]:
            return None
        job.update(state="rendering", started=_now(), detail="Rendering on the PC's graphics card.", tries=job["tries"] + 1,
                   progress=0.0, phase="Starting")
    try:
        data = _render(job)
        with users.acting_as(job["person"]):
            fit = save_fit(data, job["items"], job["rendered"], _now() - job["started"])
        with _lock:
            job.update(state="done", finished=_now(), fit=fit["id"], detail=f"Done in {plural(int(round(fit['seconds'])), 'second')}.")
    except RuntimeError as e:
        busy = "busy" in str(e).lower()
        with _lock:
            if busy and job["tries"] < MAX_TRIES:
                job.update(state="queued", started=0, retry_at=_now() + RETRY_S,
                           detail="Waiting: the PC's graphics card is busy (a game or another heavy app). I'll try again shortly.")
                return None
            job.update(state="failed", finished=_now(),
                       detail="The graphics card stayed busy. Try again when the game is closed." if busy else _reason(e))
    except Exception as e:  # noqa: BLE001
        with _lock:
            job.update(state="failed", finished=_now(), detail=_reason(e))
    return job


def _reason(e: Exception) -> str:
    text = str(e).strip()
    if isinstance(e, ValueError) and text:
        return text[:240]
    if text.startswith("The render "):         # the child runner's own words: stopped after N minutes / went quiet
        return text[:240]
    if "timed out" in text.lower() or type(e).__name__ == "TimeoutExpired":
        return "The render took too long and was stopped."
    return "The render failed on the PC: " + (text[-200:] or type(e).__name__)


def _ensure_worker() -> None:
    def run():
        idle = 0
        while idle < 3:
            step()
            with _lock:
                waiting = any(j["state"] in ("queued", "rendering") for j in _jobs)
            idle = 0 if waiting else idle + 1
            time.sleep(2)
        _worker["thread"] = None
    with _lock:
        if _worker["thread"] is None or not _worker["thread"].is_alive():
            _worker["thread"] = threading.Thread(target=run, daemon=True, name="tryon-queue")
            _worker["thread"].start()


def gpu() -> dict:
    """What the page says about rendering right now."""
    from core import pcbridge
    game = game_running()
    return {"here": pcbridge.local_pc(), "game": game,
            "note": _waiting_words(game).replace("Waiting: ", "").replace("It starts", "Try-ons start") if game else ""}


def state() -> dict:
    """Everything the Wardrobe page shows for the current person."""
    person = users.current()
    closet = garments()
    return {"person": person, "photo": photo(), "closet": closet, "looks": looks(), "fits": fits(), "week": week(), "picked": picked(),
            "jobs": jobs(person), "gpu": gpu(), "cutouts": cutout_ready(), "prepare": prepare_status(person), "seasons": list(SEASONS),
            "summary": plural(len(closet), "piece") + ", " + plural(len(looks()), "saved look")}
