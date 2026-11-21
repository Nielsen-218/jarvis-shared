"""The Training Table: the nutritionist + chef team behind the Kitchen tab (docs/KITCHEN-V2.md).

Rules, not guesses, like the trainer (core/coach_engine.py): targets come from his weight, his goal and the trainer's plan
for the day; the week's meals come from the checked dish library (knowledge/dishes/*.json), his likes and dislikes and the
pantry. The local model is only used by the chef for a "scraps" recipe when nothing in the library fits (core/chef.py).

Per person: taste.json (likes / dislikes), nutri.json (weigh-ins, water, eaten dishes, the week's plan).
Household (shared): pantry (tools/pantry.py), kitchen.json household fields (size, budget), dish photos.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from core import storage
from core.config import ROOT

DISH_DIR = ROOT / "knowledge" / "dishes"
TASTE = "taste.json"
NUTRI = "nutri.json"
HOUSEHOLD_SIZE = 5
SLOTS = ("breakfast", "lunch", "drink", "dinner")
SLOT_TIME = {"breakfast": "7:00", "lunch": "12:30", "drink": "15:30", "dinner": "18:30"}
FAMILY = ("wife", "son", "family")     # who a dish is a known favourite of (knowledge/dishes/*.json "family_favorite")
_lock = threading.Lock()
_cache: dict = {"key": None, "dishes": {}}


class UnknownDish(KeyError):
    """A dish id that isn't in the library (the API answers 404; any other KeyError is a real bug)."""


# -- the dish library ------------------------------------------------------------------------------------------------
def library() -> dict:
    """All checked dishes by id (re-read when a file changes). Bad files are skipped, never fatal."""
    files = sorted(DISH_DIR.glob("*.json")) if DISH_DIR.exists() else []
    created = storage.DATA / "household" / "chef-creations.json"
    key = tuple((f.name, f.stat().st_mtime_ns) for f in files) + \
        ((created.stat().st_mtime_ns,) if created.exists() else ())
    if key != _cache["key"]:
        dishes = {}
        for f in files:
            try:
                for d in json.loads(f.read_text(encoding="utf-8")):
                    if isinstance(d, dict) and d.get("id") and d.get("name"):
                        dishes.setdefault(d["id"], d)
            except (OSError, ValueError):
                continue
        extra = storage.load("household/chef-creations.json", {})   # the chef's scraps recipes, likeable like the rest
        for k, d in (extra.items() if isinstance(extra, dict) else []):
            dishes.setdefault(k, d)
        _cache.update(key=key, dishes=dishes)
    return _cache["dishes"]


def dish(dish_id: str) -> dict | None:
    return library().get(dish_id)


def forget_cache() -> None:
    _cache["key"] = None


# -- taste --------------------------------------------------------------------------------------------------------------
def taste() -> dict:
    t = storage.load(TASTE, {})
    return {"likes": dict(t.get("likes", {})), "dislikes": dict(t.get("dislikes", {}))}


def vote(dish_id: str, choice: str) -> dict:
    """like / dislike / clear one dish for the current person. Returns the new taste."""
    if choice not in ("like", "dislike", "clear"):
        raise ValueError("vote must be like, dislike or clear")
    if dish_id not in library():
        raise UnknownDish(dish_id)
    with _lock:
        t = taste()
        t["likes"].pop(dish_id, None)
        t["dislikes"].pop(dish_id, None)
        if choice != "clear":
            t["likes" if choice == "like" else "dislikes"][dish_id] = datetime.now().isoformat(timespec="seconds")
        storage.save(TASTE, t)
    if choice == "dislike":
        _drop_from_plan(dish_id)       # "off the plan" means off THIS week's plan too, not only future ones
    return t


def _drop_from_plan(dish_id: str) -> None:
    key = _monday().isoformat()
    plan = _record()["plans"].get(key)
    for day in plan or []:
        for slot, m in list(day.get("meals", {}).items()):
            if m == dish_id:
                swap(day["date"], slot)


def taste_of(dish_id: str, t: dict | None = None) -> str | None:
    t = t or taste()
    return "like" if dish_id in t["likes"] else "dislike" if dish_id in t["dislikes"] else None


# -- pantry matching ----------------------------------------------------------------------------------------------------
_DROP = {"dried", "fresh", "frozen", "canned", "boneless", "skinless", "large", "small", "medium", "ripe", "green", "whole",
         "chopped", "sliced", "diced", "ground", "lean", "low", "sodium", "reduced", "fat", "raw", "cooked", "white", "long",
         "grain", "long-grain", "extra", "virgin", "of", "and", "or", "the", "a", "lb", "oz", "bag", "pack", "can", "jar",
         "organic", "great", "value", "brand", "bunch", "head", "piece", "pieces", "cup", "cups", "tbsp", "tsp"}
_SAME = {"chickens": "chicken", "thighs": "thigh", "drumsticks": "chicken", "breasts": "breast", "eggs": "egg",
         "beans": "bean", "plantains": "plantain", "bananas": "banana", "potatoes": "potato", "tomatoes": "tomato",
         "onions": "onion", "peppers": "pepper", "carrots": "carrot", "limes": "lime", "lemons": "lemon", "pwa": "bean",
         "diri": "rice", "bannann": "plantain", "ze": "egg", "poul": "chicken", "pwason": "fish", "kabrit": "goat",
         "griot": "pork", "shoulder": "pork", "noodles": "spaghetti", "pasta": "spaghetti", "kidney": "bean"}


def words(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-zà-ÿ]+", (text or "").lower()):
        w = _SAME.get(w, w)
        if len(w) > 2 and w not in _DROP:
            out.add(w[:-1] if w.endswith("s") and len(w) > 4 and not w.endswith("ss") else w)
    return out


def _has(item: str, pantry_words: list) -> bool:
    need = words(item)
    if not need:
        return True
    # the ingredient's head noun (its last meaningful word) must be in one pantry entry: "chicken thighs" = "chicken"
    heads = [w for w in re.findall(r"[a-zà-ÿ]+", item.lower()) if _SAME.get(w, w) in need or w in need]
    head = _SAME.get(heads[-1], heads[-1]) if heads else next(iter(need))
    head = head[:-1] if head.endswith("s") and len(head) > 4 and not head.endswith("ss") else head
    return any(head in p or (need & p and len(need & p) >= max(1, len(need) - 1)) for p in pantry_words)


def pantry_match(d: dict, pantry: list) -> tuple[float, list, list]:
    """(share of key ingredients on hand, have, missing)."""
    pw = [words(p.get("name", "")) for p in pantry]
    ings = [i for i in d.get("ingredients") or [] if isinstance(i, dict) and i.get("item")]
    keys = [i for i in ings if i.get("key")] or ings[:3]
    have = [i["item"] for i in keys if _has(i["item"], pw)]
    missing = [i["item"] for i in keys if i["item"] not in have]
    return (len(have) / len(keys) if keys else 0.0), have, missing


# -- the day and the targets --------------------------------------------------------------------------------------------
def day_type(day: date | None = None) -> tuple[str, str]:
    """(heavy | run | train | rest, label) from the trainer's plan for that day."""
    try:
        from tools import fitness
        p = fitness._today_plan(fitness._data(), day or date.today())
    except Exception:  # noqa: BLE001 - no plan or a broken one: a normal training day
        p = None
    if not p:
        return "train", "Training day"
    kind = p.get("kind") or "mixed"
    title = str(p.get("title") or "")
    if kind == "rest":
        return "rest", title or "Rest and recovery"
    if kind in ("run", "sdc"):
        return "run", title or "Run day"
    return "heavy", title or "Strength day"


_DAY_KCAL = {"heavy": 1.06, "run": 1.04, "train": 1.0, "rest": 0.9}


def _record() -> dict:
    n = storage.load(NUTRI, {})
    for k, empty in (("weighins", []), ("water", {}), ("eaten", {}), ("plans", {})):
        n.setdefault(k, empty)
    return n


def _save(n: dict) -> None:
    storage.save(NUTRI, n)


def weight_lbs() -> float | None:
    w = _record()["weighins"]
    if w:
        return float(sorted(w, key=lambda r: r["date"])[-1]["lbs"])
    try:
        from tools import fitness
        v = fitness._data().get("profile", {}).get("weight_lbs")
        return float(str(v).split()[0]) if v else None
    except Exception:  # noqa: BLE001
        return None


def targets(day: date | None = None) -> dict | None:
    """Muscle + speed (a tactical athlete): protein ~1 g/lb, fat ~0.4 g/lb, carbs fill the rest and follow the day's
    training. Calories: his saved maintenance from the kitchen targets if he set them, else maintenance (an active
    soldier, ~15 kcal/lb), plus 250 for lean muscle gain. None until a weight is known."""
    lbs = weight_lbs()
    if not lbs or lbs < 80 or lbs > 450:
        return None
    kind, _ = day_type(day)
    saved = storage.load("nutrition.json", {}).get("targets", {})
    # kitchen.nutrition_targets saves maintenance as "tdee" ("kcal" there is already its goal figure)
    base = float(saved["tdee"]) + 250 if saved.get("tdee") else lbs * 15 + 250
    kcal = round(base * _DAY_KCAL[kind] / 10) * 10
    protein = round(min(lbs, 230) * 1.0)
    fat = round(lbs * 0.4)
    carbs = max(100, round((kcal - protein * 4 - fat * 9) / 4))
    water = round(min(5.0, lbs * 0.5 * 0.0296 + (0.7 if kind in ("heavy", "run") else 0.3)), 1)
    return {"kcal": kcal, "protein_g": protein, "carbs_g": carbs, "fat_g": fat, "water_l": water}


def log_weight(lbs: float, day: str | None = None) -> dict:
    if not 80 <= float(lbs) <= 450:
        raise ValueError("weight looks wrong")
    with _lock:
        n = _record()
        d = day or date.today().isoformat()
        n["weighins"] = [w for w in n["weighins"] if w["date"] != d] + [{"date": d, "lbs": round(float(lbs), 1)}]
        n["weighins"] = sorted(n["weighins"], key=lambda r: r["date"])[-400:]
        _save(n)
    return {"date": d, "lbs": round(float(lbs), 1)}


def add_water(liters: float) -> float:
    with _lock:
        n = _record()
        d = date.today().isoformat()
        n["water"][d] = round(max(0.0, n["water"].get(d, 0.0) + float(liters)), 2)
        n["water"] = dict(sorted(n["water"].items())[-60:])
        _save(n)
        return n["water"][d]


def eat(dish_id: str, slot: str = "", portion: float = 1.0) -> dict:
    """Mark a dish eaten today: its macros go into the food log (tools/kitchen's nutrition.json, the one the rest of
    Jarvis reads) and the plan shows it done."""
    d = dish(dish_id)
    if not d:
        raise UnknownDish(dish_id)
    portion = max(0.25, min(3.0, float(portion or 1.0)))
    m = {k: round((d.get("macros", {}).get(k) or 0) * portion) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
    today = date.today().isoformat()
    with _lock:
        log = storage.load("nutrition.json", {"targets": {}, "log": []})
        log.setdefault("log", []).append({"date": today, "time": datetime.now().strftime("%H:%M"), "food": d["name"],
                                          "kcal": m["kcal"], "protein_g": m["protein_g"], "carbs_g": m["carbs_g"],
                                          "fat_g": m["fat_g"], "dish": dish_id, "slot": slot})
        log["log"] = log["log"][-3000:]
        storage.save("nutrition.json", log)
        n = _record()
        n["eaten"].setdefault(today, []).append({"dish": dish_id, "slot": slot, "portion": portion})
        n["eaten"] = dict(sorted(n["eaten"].items())[-60:])
        _save(n)
    return m


def uneat(dish_id: str, slot: str = "") -> dict:
    """Undo the latest 'eaten' of this dish (and slot) today: out of the food log and the plan's done marks."""
    today = date.today().isoformat()
    with _lock:
        log = storage.load("nutrition.json", {"targets": {}, "log": []})
        rows = log.get("log", [])
        for i in range(len(rows) - 1, -1, -1):
            r = rows[i]
            if r.get("date") == today and r.get("dish") == dish_id and (not slot or r.get("slot") == slot):
                del rows[i]
                break
        storage.save("nutrition.json", log)
        n = _record()
        eaten = n["eaten"].get(today, [])
        for i in range(len(eaten) - 1, -1, -1):
            if eaten[i].get("dish") == dish_id and (not slot or eaten[i].get("slot") == slot):
                del eaten[i]
                break
        _save(n)
    return eaten_today()


def pantry_view(items: list) -> list:
    """Pantry rows for the page: 'added' as a calendar day (the pantry keeps a timestamp)."""
    out = []
    for it in items:
        a = it.get("added")
        if isinstance(a, (int, float)):
            a = datetime.fromtimestamp(a).date().isoformat()
        out.append({**it, "added": a})
    return out


def shopping_notes() -> list:
    """Open items on the household shopping list (what he or the page added by hand)."""
    try:
        from tools.notes import _all_notes
        return [n for n in _all_notes() if n.get("list") == "shopping" and not n.get("done")]
    except Exception:  # noqa: BLE001
        return []


def add_to_shopping(items: list) -> int:
    from tools import notes
    def key(text):
        return " ".join(sorted(words(text))) or text.strip().lower()

    have = {key(n.get("text", "")) for n in shopping_notes()}
    added = 0
    for it in items[:60]:
        text = (str(it.get("item", "")).strip() + (f" ({it['qty']})" if it.get("qty") else ""))[:120]
        if not text.strip() or key(text.split(" (")[0]) in have:
            continue
        notes.add_note(text, "shopping")
        have.add(key(text.split(" (")[0]))
        added += 1
    return added


def eaten_today() -> dict:
    today = date.today().isoformat()
    rows = [r for r in storage.load("nutrition.json", {}).get("log", []) if r.get("date") == today]
    total = {k: sum(int(r.get(k) or 0) for r in rows) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
    total["water_l"] = _record()["water"].get(today, 0.0)
    return total


# -- the week's plan ----------------------------------------------------------------------------------------------------
def _monday(day: date | None = None) -> date:
    d = day or date.today()
    return d - timedelta(days=d.weekday())


def household_taste() -> dict:
    """Likes and dislikes of everyone else in the household (each person votes on their own phone): {"likes": {id: n},
    "dislikes": {id: n}}. The plan feeds the family, so their votes count too, a little less than his own."""
    from core import users
    me = users.current()
    out = {"likes": {}, "dislikes": {}}
    for uid in users.all_users():
        if uid == me:
            continue
        try:
            t = storage._read(storage.user_dir(uid) / TASTE, {})
        except OSError:
            continue
        if not isinstance(t, dict):
            continue
        for k in ("likes", "dislikes"):
            if not isinstance(t.get(k), dict):
                continue
            for i in (t.get(k) or {}):
                out[k][i] = out[k].get(i, 0) + 1
    return out


def _score(d: dict, slot: str, kind: str, t: dict, pantry: list, budget_tight: bool, fam: dict | None = None) -> float:
    tags = set(d.get("tags", []))
    s = 0.0
    if d["id"] in t["likes"]:
        s += 3
    fav = set(d.get("family_favorite") or [])
    if fav & set(FAMILY):
        s += 1                                   # his wife's and son's favourites (labouyi, espageti, mashed potatoes)
    if "owner" in fav:
        s += 0.5
    if fam:
        s += 1.0 * fam["likes"].get(d["id"], 0) - 1.5 * fam["dislikes"].get(d["id"], 0)
    if "high-protein" in tags:
        s += 2
    if kind in ("heavy", "run") and "training-day" in tags:
        s += 1.5
    if kind == "rest" and "rest-day" in tags:
        s += 1.5
    if budget_tight and "budget" in tags:
        s += 1.5
    if slot == "breakfast" and (d.get("macros", {}).get("protein_g") or 0) < 20:
        s -= 1.5 if fav & set(FAMILY) else 3     # muscle goal: breakfast carries real protein (a family favourite like
                                                 # labouyi still comes up; eggs on the side do the rest)
    if "toddler-ok" in tags:
        s += 0.5
    if slot != "breakfast" and ("batch" in tags or "one-pot" in tags):
        s += 0.5
    s += 2 * pantry_match(d, pantry)[0]
    if (d.get("cuisine") or "") == "Haitian":
        s += 1                                   # the family's own food comes first
    return s


PROTEINS = ("steak", "beef", "pork", "chicken", "goat", "fish", "shrimp", "turkey", "conch", "salmon", "oxtail", "snapper",
            "herring", "saltfish", "cod", "tuna", "sardine", "lambi", "egg")


def protein_of(d: dict) -> str:
    """The main protein of a dish (from its key ingredients), for variety: steak five nights a week is not a plan."""
    for i in d.get("ingredients") or []:
        if isinstance(i, dict) and i.get("key"):
            w = words(i.get("item", ""))
            for p in PROTEINS:
                if p in w:
                    return "beef" if p == "steak" else p
    return ""


def _fits(d: dict, slot: str) -> bool:
    cat = d.get("category")
    if slot == "drink":
        return cat in ("drink", "shake")      # a natural juice or a shake every afternoon (owner, 2026-11-20)
    if slot == "breakfast":
        return cat not in ("drink", "shake", "sauce") and (cat == "breakfast" or "breakfast" in d.get("meals", []))
    return cat in ("main", "soup") and slot in d.get("meals", ["lunch", "dinner"])


def build_week(start: date | None = None, pantry: list | None = None, budget_tight: bool = False) -> list:
    """Seven days of breakfast, lunch and dinner. Dinner is cooked for 5 and a batch dinner becomes the next day's lunch
    (one cook, two meals). No dish twice within three days; disliked dishes never. Deterministic for a given week."""
    from tools import pantry as pantry_tool
    pantry = pantry if pantry is not None else pantry_tool.list_items()
    lib = library()
    t = taste()
    start = start or _monday()
    pool = [d for d in lib.values() if d["id"] not in t["dislikes"] and not d.get("chef_scraps")]
    fam = household_taste()
    days, recent, recent_drinks = [], [], []
    seed = int(hashlib.sha1(start.isoformat().encode()).hexdigest()[:8], 16)
    carry = None
    for i in range(7):
        day = start + timedelta(days=i)
        kind, label = day_type(day)
        meals = {}
        carried = False
        for slot in SLOTS:
            if slot == "lunch" and carry:
                meals[slot] = carry
                carried = True
                continue
            window = recent_drinks[-3:] if slot == "drink" else recent[-8:]   # food: about 3 days; drinks their own
            cands = [d for d in pool if _fits(d, slot) and d["id"] not in window] or [d for d in pool if _fits(d, slot)]
            if not cands:
                continue
            used_p = [protein_of(lib[r]) for r in recent if r in lib]
            cands.sort(key=lambda d: (-(_score(d, slot, kind, t, pantry, budget_tight, fam)
                                        - (1.5 * used_p.count(protein_of(d)) if protein_of(d) and slot in ("lunch", "dinner")
                                           else 0)),
                                      (int(hashlib.sha1((d["id"] + str(seed + i)).encode()).hexdigest()[:6], 16))))
            top = cands[:4]                       # variety among the best few
            pick = top[(seed + i * 7 + len(slot)) % len(top)]
            meals[slot] = pick["id"]
            (recent_drinks if slot == "drink" else recent).append(pick["id"])
        dinner = lib.get(meals.get("dinner", ""))
        carry = meals.get("dinner") if dinner and ({"batch", "one-pot", "freezer"} & set(dinner.get("tags", []))) else None
        days.append({"date": day.isoformat(), "day_type": kind, "label": label, "meals": meals,
                     "lunch_is_leftovers": carried})
    return days


def week(refresh: bool = False, budget_tight: bool = False) -> list:
    key = _monday().isoformat()
    with _lock:
        n = _record()
        plan = n["plans"].get(key)
    lib = library()
    t = taste()
    usable = [x for x in lib.values() if x["id"] not in t["dislikes"] and not x.get("chef_scraps")]   # build_week's pool
    stale = plan and (any(m not in lib for d in plan for m in d.get("meals", {}).values())
                      or any(set(d.get("meals", {})) < set(SLOTS) and any(_fits(x, s) for x in usable for s in
                                                                            set(SLOTS) - set(d.get("meals", {})))
                             for d in plan))
    if refresh or not plan or stale:
        plan = build_week(budget_tight=budget_tight)
        with _lock:
            n = _record()
            n["plans"][key] = plan
            n["plans"] = dict(sorted(n["plans"].items())[-8:])
            _save(n)
    return plan


def swap(day: str, slot: str) -> list:
    """Replace one planned meal with the next best one (after a dislike, or 'not this')."""
    plan = week()
    lib, t = library(), taste()
    from tools import pantry as pantry_tool
    pantry = pantry_tool.list_items()
    used = {m for d in plan for m in d["meals"].values()}
    for d in plan:
        if d["date"] == day and slot in SLOTS:
            ok = [x for x in lib.values() if _fits(x, slot) and x["id"] not in t["dislikes"] and not x.get("chef_scraps")
                  and x["id"] != d["meals"].get(slot)]
            cands = [x for x in ok if x["id"] not in used] or ok   # a small library: repeat a dish rather than keep a disliked one
            if cands:
                new = max(cands, key=lambda x: _score(x, slot, d["day_type"], t, pantry, False))
                d["meals"][slot] = new["id"]
                if slot == "lunch":
                    d["lunch_is_leftovers"] = False          # a fresh lunch: it must be cooked (and bought)
                if slot == "dinner":
                    nxt = next((x for x in plan if x["date"] > d["date"]), None)
                    if nxt and nxt.get("lunch_is_leftovers"):
                        if {"batch", "one-pot", "freezer"} & set(new.get("tags", [])):
                            nxt["meals"]["lunch"] = new["id"]   # tomorrow's lunch is still tonight's leftovers
                        else:
                            nxt["lunch_is_leftovers"] = False
    with _lock:
        n = _record()
        n["plans"][_monday().isoformat()] = plan
        _save(n)
    return plan


def dishes_for(ids, t: dict | None = None) -> dict:
    """Full dish objects (with photo, taste and pantry match) for the page."""
    from tools import pantry as pantry_tool
    t = t or taste()
    pantry = pantry_tool.list_items()
    lib = library()
    return {i: {**lib[i], "photo": photo_url(i), "taste": taste_of(i, t), "pantry_match": round(pantry_match(lib[i], pantry)[0], 2)}
            for i in ids if i in lib}


def shopping(plan: list, pantry: list) -> list:
    """What the week's plan needs that the pantry doesn't have, merged by item, grouped by aisle. Cooked once for 5."""
    lib = library()
    seen = {}
    pw = [words(p.get("name", "")) for p in pantry]
    cooked, prev_dinner = [], None
    for d in plan:
        for slot, m in d["meals"].items():
            if slot == "lunch" and m == prev_dinner:
                continue                      # yesterday's batch dinner: already bought
            cooked.append(m)
        prev_dinner = d["meals"].get("dinner")
    for m in cooked:
        dd = lib.get(m)
        for ing in (dd or {}).get("ingredients", []):
            item = ing.get("item", "")
            if not item or _has(item, pw) or not ing.get("key", True) and _staple(item):
                continue
            k = " ".join(sorted(words(item))) or item.lower()
            row = seen.setdefault(k, {"item": item, "qty": [], "aisle": ing.get("aisle", "other"), "for": []})
            if ing.get("qty"):
                row["qty"].append(ing["qty"])
            if dd["id"] not in row["for"]:
                row["for"].append(dd["id"])
    out = [{**r, "qty": " + ".join(r["qty"][:4])} for r in seen.values()]
    return sorted(out, key=lambda r: (r["aisle"], r["item"]))


_STAPLES = words("salt pepper oil garlic onion lime thyme scallion bouillon cloves epis water sugar parsley vinegar")


def _staple(item: str) -> bool:
    return bool(words(item)) and words(item) <= _STAPLES


def cook_now(pantry: list, limit: int = 8, extra: list | None = None) -> list:
    """Dishes the household can cook right now (or nearly), best first: most key ingredients on hand, then liked."""
    t = taste()
    pool = pantry + [{"name": x} for x in (extra or [])]
    rows = []
    for d in library().values():
        if d["id"] in t["dislikes"] or d.get("category") in ("sauce", "drink"):
            continue
        share, have, missing = pantry_match(d, pool)
        if share >= 0.5:
            rows.append((share + (0.3 if d["id"] in t["likes"] else 0) + (0.1 if d.get("cuisine") == "Haitian" else 0),
                         {"dish": d["id"], "have": have, "missing": missing}))
    rows.sort(key=lambda r: -r[0])
    return [r[1] for r in rows[:limit]]


def page(budget_tight: bool = False) -> dict:
    """Everything GET /api/kitchen/v2 returns (docs/KITCHEN-V2.md section 3)."""
    from core import users
    from tools import kitchen as kitchen_tool, pantry as pantry_tool
    lib = library()
    t = taste()
    pantry = pantry_tool.list_items()
    plan = week(budget_tight=budget_tight)
    today = date.today().isoformat()
    kind, label = day_type()
    tg = targets()
    eaten_rows = _record()["eaten"].get(today, [])
    done = {(r.get("slot"), r.get("dish")) for r in eaten_rows}
    tday = next((d for d in plan if d["date"] == today), None)
    meals = [{"slot": s, "time": SLOT_TIME[s], "dish": m, "done": (s, m) in done}
             for s, m in ((tday or {}).get("meals", {}) or {}).items()]
    hh = kitchen_tool._data().get("household") or {}
    try:
        size = int(hh.get("household_size") or HOUSEHOLD_SIZE)   # "members" is his own free-text description
    except (TypeError, ValueError):
        size = HOUSEHOLD_SIZE
    budget = hh.get("weekly_food_budget")
    used = {m for d in plan for m in d["meals"].values()} | {r["dish"] for r in eaten_rows}
    cook = cook_now(pantry)
    used |= {c["dish"] for c in cook}
    used |= set(t["likes"]) | set(t["dislikes"])
    dishes = {}
    for i in used:
        d = lib.get(i)
        if not d:
            continue
        share = pantry_match(d, pantry)[0]
        dishes[i] = {**d, "photo": photo_url(i), "taste": taste_of(i, t), "pantry_match": round(share, 2)}
    return {
        "me": {"name": (users.get(users.current()) or {}).get("name", ""), "goal": "muscle + speed", "day_type": kind,
               "day_label": label, "targets": tg, "eaten": eaten_today(), "set_up": tg is not None,
               "weight_lbs": weight_lbs(), "weighins": _record()["weighins"][-30:]},
        "household": {"size": size, "budget_week_usd": budget, "toddler": True},
        "today": {"meals": meals, "coach_note": coach_note(kind)},
        "week": plan,
        "dishes": dishes,
        "cook_now": cook,
        "pantry": pantry_view(pantry),
        "shopping": shopping(plan, pantry) + [{"item": n.get("text", ""), "qty": "", "aisle": "on the list", "for": [], "note": True}
                                              for n in shopping_notes()],
        "snaps": recent_snaps(),
        "library_size": len(lib),
    }


def coach_note(kind: str) -> str:
    return {"heavy": "Strength day: have carbs and 30-40 g protein 2-3 hours before, and protein with rice or plantain "
                     "within 2 hours after.",
            "run": "Run day: a light carb snack (banana, akasan) 60-90 minutes before; refuel with carbs + protein after.",
            "rest": "Rest day: protein at every meal, lighter on rice and bread, plenty of vegetables and water.",
            "train": "Protein at every meal and water through the day."}[kind]


# -- dish photos and snaps (files live in data/household so they sync to the laptop) -----------------------------------
PHOTO_DIR = "household/dish-photos"
SNAP_DIR = "household/snaps"


def photo_path(dish_id: str) -> Path:
    return storage.DATA / PHOTO_DIR / f"{re.sub(r'[^a-z0-9-]', '', dish_id.lower())}.webp"


def photo_url(dish_id: str) -> str | None:
    p = photo_path(dish_id)
    return f"/api/kitchen/photo/{dish_id}?v={int(p.stat().st_mtime)}" if p.exists() else None


def recent_snaps(limit: int = 12) -> list:
    idx = storage.load(f"{SNAP_DIR}/index.json", [])
    return [{"id": s["id"], "kind": s["kind"], "at": s["at"], "thumb": f"/api/kitchen/snap/{s['id']}.jpg",
             "items": s.get("items", [])[:12]} for s in idx[-limit:][::-1]]
