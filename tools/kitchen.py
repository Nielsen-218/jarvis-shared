"""Kitchen: personal chef, nutritionist and grocery-deal finder for the whole household.

- Household food profile (shared): who eats (5 people incl. an 18-month-old), allergies, dislikes, cuisines
  (Haitian and American by default), stores, ZIP code, cooking time.
- Nutrition (per person): calorie and macro targets from body stats and goal (e.g. lean muscle + chest
  definition), plus a simple daily food log against those targets.
- Chef: weekly meal plans and recipes Jarvis designs (family-size, budget-first, with a toddler-safe
  version of every meal), turned into one shopping list on the shared list.
- Deals: live prices from local stores' weekly flyers (Flipp) so the list is bought where it's cheapest;
  Jarvis also reminds about the commissary.

Data: data/household/kitchen.json (shared) and data/users/<id>/nutrition.json (personal).
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

from core import storage
from core.words import plural
from tools.registry import tool

FILE = "household/kitchen.json"
NUTRITION = "nutrition.json"
HOUSEHOLD_FIELDS = ["zip", "home_address", "members", "allergies", "dislikes", "cuisines", "stores", "memberships",
                    "cooking_time", "equipment",
                    "weekly_food_budget", "notes"]
TODDLER_RULES = ("Toddler (18 months): same family food, mashed or cut small and soft; cut grapes, cherry tomatoes and "
                 "blueberries in quarters, hot dogs/sausages lengthwise then small, no whole nuts or popcorn; little added "
                 "salt or sugar (season the adults' portions after splitting his off); about 16-24 oz of whole milk a "
                 "day, not more; iron-rich foods daily (beans, meat, eggs, fortified cereal); water with meals.")


def _data() -> dict:
    return storage.load(FILE, {"household": {"cuisines": "Haitian and American"}, "plan": {}, "recipes": {}})


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "recipe"


def _day_totals(log: list, day: str) -> tuple[int, int]:
    rows = [r for r in log if r["date"] == day]
    return sum(r["kcal"] for r in rows), sum(r["protein_g"] for r in rows)


# -- household and nutrition targets -------------------------------------------------------------
@tool(
    "Save the household's food details: zip (for local deals), home_address (start of the shopping route), members (e.g. '2 adults, 2 teens, 18-month-old "
    "son'), allergies, dislikes, cuisines, stores they shop at, memberships (Costco, Sam's Club, commissary...), cooking_time, equipment, weekly_food_budget, notes.",
    {"field": {"type": "string", "enum": HOUSEHOLD_FIELDS}, "value": {"type": "string"}},
)
def set_household_food(field: str, value: str) -> str:
    data = _data()
    data["household"][field] = value.strip()
    storage.save(FILE, data)
    return f"Saved {field.replace('_', ' ')}: {value}."


def _fitness_profile() -> dict:
    return storage.load("fitness.json", {}).get("profile", {})


def _height_cm(v: str) -> float | None:
    s = str(v or "").strip().lower().replace("’", "'").replace("”", '"')
    m = re.match(r"(\d)\s*'\s*(\d+(?:\.\d+)?)?", s)
    if m:
        return (int(m.group(1)) * 12 + float(m.group(2) or 0)) * 2.54
    m = re.match(r"(\d{3})\s*cm", s)
    return float(m.group(1)) if m else None


@tool(
    "Work out this person's daily calories, protein, carbs and fat from their body stats and goal (Mifflin-St Jeor "
    "plus activity), and save them. Goals: 'lean muscle' (recomposition: small deficit, high protein, good for chest "
    "definition), 'cut', 'maintain', 'bulk'. Uses the trainer profile for weight/height/age when not given.",
    {"goal": {"type": "string", "enum": ["lean muscle", "cut", "maintain", "bulk"]},
     "weight_lbs": {"type": "number"}, "height": {"type": "string", "description": "e.g. 6'2.5\" or 189 cm"},
     "age": {"type": "integer"}, "sex": {"type": "string", "enum": ["male", "female"]},
     "activity": {"type": "string", "enum": ["light", "moderate", "very active", "athlete"]}},
    required=["goal"],
)
def nutrition_targets(goal: str, weight_lbs: float = 0, height: str = "", age: int = 0, sex: str = "",
                      activity: str = "very active") -> str:
    fp = _fitness_profile()
    w = float(weight_lbs or re.sub(r"[^0-9.]", "", str(fp.get("weight_lbs", ""))) or 0)
    h = _height_cm(height or fp.get("height", ""))
    a = int(age or re.sub(r"\D", "", str(fp.get("age", ""))) or 0)
    sx = (sex or fp.get("sex") or "male").lower()
    missing = [n for n, v in (("weight", w), ("height", h), ("age", a)) if not v]
    if missing:
        return f"Need {', '.join(missing)} first (ask, or save them with set_fitness_profile)."
    kg = w * 0.4536
    bmr = 10 * kg + 6.25 * h - 5 * a + (5 if sx.startswith("m") else -161)
    tdee = bmr * {"light": 1.375, "moderate": 1.55, "very active": 1.725, "athlete": 1.9}[activity]
    kcal = tdee + {"lean muscle": -300, "cut": -500, "maintain": 0, "bulk": 300}[goal]
    protein = round(w * (1.0 if goal in ("lean muscle", "cut") else 0.85))
    fat = round(kcal * 0.27 / 9)
    carbs = round((kcal - protein * 4 - fat * 9) / 4)
    targets = {"goal": goal, "kcal": round(kcal, -1), "protein_g": protein, "carbs_g": carbs, "fat_g": fat,
               "tdee": round(tdee, -1), "set": date.today().isoformat()}
    n = storage.load(NUTRITION, {"targets": {}, "log": []})
    n["targets"] = targets
    storage.save(NUTRITION, n)
    return (f"Daily target ({goal}): about {targets['kcal']:.0f} kcal, {protein} g protein, {carbs} g carbs, {fat} g fat "
            f"(maintenance about {targets['tdee']:.0f}). Protein at every meal; weigh in weekly and adjust 100-200 kcal "
            "if the scale stalls for 2 weeks.")


@tool(
    "Log what this person ate (estimate calories and protein from the description if they don't give numbers), "
    "and say how the day stands against their targets.",
    {"food": {"type": "string"}, "kcal": {"type": "integer"}, "protein_g": {"type": "integer"}},
    required=["food", "kcal", "protein_g"],
)
def log_food(food: str, kcal: int, protein_g: int) -> str:
    n = storage.load(NUTRITION, {"targets": {}, "log": []})
    n["log"].append({"date": date.today().isoformat(), "food": food.strip(), "kcal": int(kcal), "protein_g": int(protein_g)})
    n["log"] = n["log"][-3000:]
    storage.save(NUTRITION, n)
    return nutrition_today()


@tool("How today's eating stands: calories and protein eaten vs targets, and what's left to hit protein.")
def nutrition_today() -> str:
    n = storage.load(NUTRITION, {"targets": {}, "log": []})
    kcal, prot = _day_totals(n["log"], date.today().isoformat())
    t = n.get("targets") or {}
    if not t:
        return f"Today so far: {kcal} kcal, {prot} g protein. No targets yet (nutrition_targets)."
    return (f"Today: {kcal} of {t['kcal']:.0f} kcal, {prot} of {t['protein_g']} g protein "
            f"({max(0, t['protein_g'] - prot)} g protein and about {max(0, t['kcal'] - kcal):.0f} kcal to go).")


# -- chef: recipes, weekly plan, shopping list ---------------------------------------------------
@tool(
    "Save a recipe you created or found, so it can be planned, shown on the phone and turned into a shopping list. "
    "Family-sized (default 5 servings), budget-first, with a toddler-safe version and per-serving macros.",
    {"name": {"type": "string"}, "servings": {"type": "integer"},
     "ingredients": {"type": "array", "items": {"type": "object", "properties": {
         "item": {"type": "string"}, "qty": {"type": "string"}, "aisle": {"type": "string"}}}},
     "steps": {"type": "array", "items": {"type": "string"}},
     "minutes": {"type": "integer"}, "cost_estimate": {"type": "number", "description": "Whole recipe, dollars"},
     "macros": {"type": "object", "description": "Per adult serving: kcal, protein_g, carbs_g, fat_g"},
     "toddler": {"type": "string", "description": "How to serve it to the 18-month-old safely"}},
    required=["name", "ingredients", "steps"],
)
def save_recipe(name: str, ingredients: list, steps: list, servings: int = 5, minutes: int = 0,
                cost_estimate: float = 0, macros: dict | None = None, toddler: str = "") -> str:
    data = _data()
    data["recipes"][_slug(name)] = {"name": name, "servings": int(servings or 5), "ingredients": ingredients,
                                    "steps": steps, "minutes": int(minutes or 0), "cost": float(cost_estimate or 0),
                                    "macros": macros or {}, "toddler": toddler, "saved": date.today().isoformat()}
    storage.save(FILE, data)
    return f"Saved recipe '{name}' ({plural(len(ingredients), 'ingredient')}, {plural(len(steps), 'step')})."


@tool("Full recipe: ingredients, steps, time, cost, macros and the toddler version.", {"name": {"type": "string"}})
def get_recipe(name: str) -> str:
    data = _data()
    r = data["recipes"].get(_slug(name)) or next((v for k, v in data["recipes"].items() if _slug(name) in k), None)
    if not r:
        return f"No saved recipe called '{name}'."
    head = f"{r['name']} (serves {r['servings']}" + (f", {r['minutes']} min" if r["minutes"] else "") + \
        (f", about ${r['cost']:.2f}" if r["cost"] else "") + ")"
    lines = [head, "Ingredients: " + "; ".join(f"{i.get('qty', '')} {i.get('item', '')}".strip() for i in r["ingredients"])]
    lines += [f"{n}. {s}" for n, s in enumerate(r["steps"], 1)]
    if r.get("macros"):
        lines.append("Per serving: " + ", ".join(f"{k} {v}" for k, v in r["macros"].items()))
    if r.get("toddler"):
        lines.append(f"For the baby: {r['toddler']}")
    return "\n".join(lines)


@tool(
    "Save this week's family meal plan you designed (breakfast, lunch, dinner, snacks for each day). Plan like a "
    "budget-smart chef and nutritionist for a family of 5 with an 18-month-old: cook once and eat twice (leftovers "
    "for lunch), build around this week's deals and the commissary, Haitian and American favorites, high protein for "
    "the adult who trains (hitting his targets), and a toddler-safe version of every meal. Use recipe names you saved "
    "with save_recipe.",
    {"week_of": {"type": "string", "description": "YYYY-MM-DD (Monday)"},
     "days": {"type": "array", "items": {"type": "object", "properties": {
         "day": {"type": "string"}, "meals": {"type": "array", "items": {"type": "object", "properties": {
             "meal": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
             "recipe": {"type": "string"}, "note": {"type": "string"}}}}}}},
     "estimated_cost": {"type": "number"}},
    required=["week_of", "days"],
)
def save_meal_plan(week_of: str, days: list, estimated_cost: float = 0) -> str:
    data = _data()
    data["plan"] = {"week_of": week_of, "days": days, "cost": float(estimated_cost or 0), "saved": date.today().isoformat()}
    storage.save(FILE, data)
    missing = sorted({m.get("recipe", "") for d in days for m in d.get("meals", [])
                      if m.get("recipe") and _slug(m["recipe"]) not in data["recipes"]})
    return (f"Meal plan for the week of {week_of} saved." +
            (f" Recipes not saved yet (save them so the shopping list is complete): {', '.join(missing)}." if missing else ""))


@tool("Today's (or a given day's) meals from the family meal plan.",
      {"day": {"type": "string", "description": "Mon..Sun, default today"}}, required=[])
def todays_meals(day: str = "") -> str:
    plan = _data().get("plan") or {}
    if not plan.get("days"):
        return "No meal plan this week: offer to make one."
    d = (day or date.today().strftime("%a")).lower()[:3]
    found = next((x for x in plan["days"] if str(x.get("day", "")).lower().startswith(d)), None)
    if not found:
        return f"Nothing planned for {day or 'today'}."
    return f"{found['day']}: " + "; ".join(f"{m['meal']}: {m.get('recipe', '')}" + (f" ({m['note']})" if m.get("note") else "")
                                          for m in found.get("meals", []))


def _shopping_items(data: dict) -> list[dict]:
    """All ingredients of the planned recipes, merged by item name."""
    merged: dict[str, dict] = {}
    for d in (data.get("plan") or {}).get("days", []):
        for m in d.get("meals", []):
            r = data["recipes"].get(_slug(m.get("recipe", "")))
            for i in (r or {}).get("ingredients", []):
                key = re.sub(r"\s+", " ", str(i.get("item", "")).lower()).strip()
                if not key:
                    continue
                e = merged.setdefault(key, {"item": i.get("item"), "qty": [], "aisle": i.get("aisle", ""),
                                            "store": (data.get("store_for") or {}).get(key, "")})
                if i.get("qty"):
                    e["qty"].append(str(i["qty"]))
    return sorted(merged.values(), key=lambda e: (e["aisle"] or "zz", e["item"].lower()))


@tool("Turn this week's meal plan into one shopping list (merged quantities, by aisle) and add it to the shared "
      "household shopping list.")
def build_shopping_list() -> str:
    from tools.notes import add_note

    items = _shopping_items(_data())
    if not items:
        return "No planned recipes with ingredients yet."
    for e in items:
        add_note(f"{e['item']}" + (f" ({' + '.join(e['qty'])})" if e["qty"] else "")
                 + (f" [{e['store']}]" if e.get("store") else ""), "shopping")
    return f"Added {plural(len(items), 'item')} to the shopping list: " + ", ".join(e["item"] for e in items[:25]) + \
        ("..." if len(items) > 25 else "")


@tool(
    "Decide where each shopping-list item gets bought this week (after checking grocery_deals): e.g. bulk rice, "
    "chicken thighs, eggs, oats, frozen vegetables -> Costco or Sam's Club; flyer deals -> Aldi / Lidl / Food Lion; "
    "meat, produce and the rest -> Commissary. The kitchen page then groups the list by store in route order.",
    {"assignments": {"type": "object", "description": "{item name: store}, e.g. {\"rice\": \"Costco\", \"plantains\": \"Commissary\"}"}},
)
def assign_stores(assignments: dict) -> str:
    data = _data()
    m = data.setdefault("store_for", {})
    for item, store in (assignments or {}).items():
        key = re.sub(r"\s+", " ", str(item).lower()).strip()
        if key and str(store).strip():
            m[key] = str(store).strip()
    storage.save(FILE, data)
    by: dict[str, int] = {}
    for st in m.values():
        by[st] = by.get(st, 0) + 1
    return "Saved where to buy: " + ", ".join(f"{k} {plural(v, 'item')}" for k, v in by.items()) + ". Next: shopping_route for those stores."


# -- deals ---------------------------------------------------------------------------------------
def _flipp(query: str, zip_code: str) -> list[dict]:
    url = "https://backflipp.wishabi.com/flipp/items/search?" + urllib.parse.urlencode(
        {"locale": "en-us", "postal_code": zip_code, "q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis grocery deals)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        items = json.loads(r.read().decode("utf-8")).get("items", [])
    words = [w.rstrip("s") for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2]
    off_target = [w for w in _PROCESSED if w not in query.lower()]
    out = []
    for i in items:
        price, name = i.get("current_price"), str(i.get("name") or "")
        low = name.lower()
        # the item has to really be what was asked for: every word present, not chips/snacks/ready-meals
        if not price or not name or not all(w in low for w in words) or any(w in low for w in off_target):
            continue
        unit = " ".join(dict.fromkeys(x.strip() for x in (i.get("pre_price_text"), i.get("post_price_text")) if x))
        out.append({"store": i.get("merchant_name"), "name": name, "price": float(price),
                    "unit": unit, "until": str(i.get("valid_to", ""))[:10]})
    return sorted(out, key=lambda x: x["price"])


_PROCESSED = ("chip", "snack", "ready", "flavored", "sweetened", "candy", "cookie", "bar", "cereal", "baby food",
              "pudding", "dried", "frozen meal", "kit", "stuffed", "mix", "seasoned", "sauce", "soup")


@tool(
    "Find this week's cheapest local deals (store weekly flyers near their ZIP) for groceries: give a list of items "
    "('chicken breast, rice, eggs, bananas, whole milk'). Returns the best prices and which store, so the shopping "
    "trip goes where it's cheapest. Remind them to compare with the commissary.",
    {"items": {"type": "string", "description": "Comma-separated items"},
     "zip_code": {"type": "string", "description": "Optional; uses the saved household ZIP"}},
    required=["items"], slow=True,
)
def grocery_deals(items: str, zip_code: str = "") -> str:
    zip_code = (zip_code or _data()["household"].get("zip", "")).strip()
    if not re.fullmatch(r"\d{5}", zip_code):
        return "Need the household ZIP code for local deals (set_household_food zip)."
    lines, stores = [], {}
    for q in [x.strip() for x in items.split(",") if x.strip()][:15]:
        try:
            deals = _flipp(q, zip_code)[:3]
        except Exception as e:
            lines.append(f"- {q}: couldn't check ({type(e).__name__})")
            continue
        if not deals:
            lines.append(f"- {q}: no flyer deals this week")
            continue
        best = deals[0]
        stores[best["store"]] = stores.get(best["store"], 0) + 1
        others = "; ".join(f"{d['store']} ${d['price']:.2f}" for d in deals[1:])
        lines.append(f"- {q}: {best['store']} ${best['price']:.2f} {best['unit']} ({best['name'][:50]}, until {best['until']})"
                     + (f". Also: {others}" if others else ""))
    book = storage.load(PRICES, {})
    known = []
    for q in [x.strip() for x in items.split(",") if x.strip()][:15]:
        rows = sorted((e for _, e in _price_matches(book, q) if e.get("unit_price") is not None), key=lambda e: e["unit_price"])
        if rows:
            known.append(f"- {q}: " + "; ".join(_fmt_unit(e) for e in rows[:3]))
    if known:
        lines.append("Warehouse / saved prices to compare per unit (Costco, Sam's Club, commissary):\n" + "\n".join(known))
    if stores:
        lines.append("Most savings at: " + ", ".join(f"{s} ({plural(n, 'item')})" for s, n in sorted(stores.items(), key=lambda x: -x[1])))
    return f"Deals near {zip_code} this week:\n" + "\n".join(lines)


# -- price book (Costco / Sam's Club / commissary prices: their sites don't publish to tools, so Jarvis remembers) --
PRICES = "household/prices.json"
_UNITS = {"lb": ("lb", 1), "lbs": ("lb", 1), "pound": ("lb", 1), "pounds": ("lb", 1), "oz": ("lb", 1 / 16),
          "kg": ("lb", 2.2046), "g": ("lb", 0.0022046), "ct": ("each", 1), "count": ("each", 1), "pk": ("each", 1),
          "pack": ("each", 1), "each": ("each", 1), "dozen": ("each", 12), "doz": ("each", 12), "gal": ("gal", 1),
          "gallon": ("gal", 1), "qt": ("gal", 0.25), "fl oz": ("gal", 1 / 128), "l": ("gal", 0.2642)}


def _unit_price(price: float, size: str) -> tuple[float, str] | None:
    """$16.72 for '60 ct' -> (0.279, 'each'); $20.99 for '25 lb' -> (0.84, 'lb'); '48 oz' -> per lb."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(?:x\s*(\d+(?:\.\d+)?)\s*)?([a-z. ]+)", str(size).lower())
    if not m:
        return None
    qty = float(m.group(1)) * (float(m.group(2)) if m.group(2) else 1)
    unit = m.group(3).strip().rstrip(".")
    unit = "fl oz" if unit.startswith("fl") else unit
    if unit not in _UNITS or qty <= 0:
        return None
    base, factor = _UNITS[unit]
    return price / (qty * factor), base


def _item_key(item: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", item.lower())).strip()


def _price_matches(book: dict, query: str) -> list[tuple[str, dict]]:
    q = set(w.rstrip("s") for w in _item_key(query).split() if len(w) > 2)
    out = []
    for key, entries in book.items():
        k = set(w.rstrip("s") for w in key.split() if len(w) > 2)
        if q and k and (q <= k or k <= q):
            out += [(key, e) for e in entries]
    return out


def _fmt_unit(e: dict) -> str:
    unit = e.get("unit")
    up = e.get("unit_price")
    per = f" (${up:.2f}/{unit})" if up is not None and unit else ""
    return f"{e['store']} ${e['price']:.2f} for {e['size']}{per}, {e['source']} {e['date']}"


@tool(
    "Remember a store price (Costco, Sam's Club, the commissary or any store), from what they read off a shelf, "
    "an app, a website they're looking at, or a receipt photo (log every line you can read). Prices are compared "
    "per unit (per lb / per egg / per gallon) against flyer deals when planning groceries.",
    {"item": {"type": "string", "description": "e.g. 'boneless chicken breast', 'large eggs', 'long grain white rice'"},
     "store": {"type": "string"}, "price": {"type": "number", "description": "Package price in dollars"},
     "size": {"type": "string", "description": "Package size: '6.5 lb', '60 ct', '25 lb', '2 gal', '48 oz'"},
     "source": {"type": "string", "description": "'shelf', 'receipt', 'website', 'app' or 'estimate'"}},
    required=["item", "store", "price", "size"],
)
def log_price(item: str, store: str, price: float, size: str, source: str = "shelf") -> str:
    book = storage.load(PRICES, {})
    key = _item_key(item)
    up = _unit_price(float(price), size)
    entry = {"store": store.strip(), "price": round(float(price), 2), "size": size.strip(), "source": source or "shelf",
             "date": date.today().isoformat(), "unit_price": round(up[0], 3) if up else None, "unit": up[1] if up else ""}
    rows = [e for e in book.get(key, []) if e["store"].lower() != entry["store"].lower()]  # newest price per store
    book[key] = rows + [entry]
    storage.save(PRICES, book)
    ranked = sorted((e for e in book[key] if e.get("unit_price") is not None), key=lambda e: e["unit_price"])
    best = f" Cheapest known: {_fmt_unit(ranked[0])}." if len(ranked) > 1 else ""
    return f"Saved: {item} at {_fmt_unit(entry)}.{best}"


@tool("What Jarvis knows about store prices (Costco, Sam's Club, commissary, etc.), cheapest per unit first, for an "
      "item or everything. Estimates are marked; prefer prices they logged.",
      {"item": {"type": "string", "description": "Optional item to look up"}}, required=[])
def price_book(item: str = "") -> str:
    book = storage.load(PRICES, {})
    rows = _price_matches(book, item) if item else [(k, e) for k, v in book.items() for e in v]
    if not rows:
        return "No saved prices for that yet: ask them to read the Costco / Sam's price or snap a receipt."
    by: dict[str, list] = {}
    for k, e in rows:
        by.setdefault(k, []).append(e)
    return "\n".join(f"- {k}: " + "; ".join(_fmt_unit(e) for e in sorted(v, key=lambda e: e.get("unit_price") or 1e9))
                     for k, v in sorted(by.items()))


# -- shopping route ------------------------------------------------------------------------------
# Search names that find the right kind of store on OpenStreetMap ("commissary" -> the closest base commissary).
STORE_SEARCH = {"commissary": ["Lincoln Commissary", "Commissary"], "sams": ["Sam's Club"], "sam's": ["Sam's Club"],
                "costco": ["Costco"], "walmart": ["Walmart Supercenter", "Walmart"]}
_NOT_A_STORE = ("tire", "gas", "fuel", "pharmacy", "optical", "car wash", "hearing")


def _osm_near(query: str, lat: float, lon: float) -> list[dict]:
    from tools import travel

    wait = 1.1 - (time.monotonic() - travel._last_nominatim[0])  # Nominatim's one-request-a-second rule
    if wait > 0:
        time.sleep(wait)
    travel._last_nominatim[0] = time.monotonic()
    box = f"{lon - 0.45},{lat + 0.35},{lon + 0.45},{lat - 0.35}"  # about 30 miles around home
    q = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 40, "viewbox": box, "bounded": 1,
                                "addressdetails": 1})       # structured street + town for _street()
    found = travel._get(f"https://nominatim.openstreetmap.org/search?{q}")
    return [p for p in found if p.get("category") in ("shop", "building", "amenity") and p.get("type") != "fuel"
            and not any(w in p["display_name"].lower() for w in _NOT_A_STORE)]


def _drive_minutes(home: tuple[float, float], places: list[dict]) -> list[float]:
    """Real driving minutes from home (straight-line distance lies around here: the water is in the way)."""
    from tools import travel

    coords = ";".join([f"{home[1]},{home[0]}"] + [f"{p['lon']},{p['lat']}" for p in places])
    r = travel._get(f"https://router.project-osrm.org/table/v1/driving/{coords}?sources=0", timeout=20)
    return [d / 60 if d is not None else 1e9 for d in r["durations"][0][1:]]


_HOME_SPOTS: dict[str, tuple[float, float]] = {}  # address -> (lat, lon): home doesn't move, the lookup costs ~1 s


def _home(household: dict) -> tuple[float, float, str]:
    """Home on the map. Raises LookupError when it isn't set or can't be found. (With no address and no ZIP this
    used to geocode ", USA" — the middle of the country — and plan the shopping run from Kansas.)"""
    from tools import travel

    address = str(household.get("home_address") or "").strip()
    zip_code = str(household.get("zip") or "").strip()
    if not address and not zip_code:
        raise LookupError("no home address or ZIP code is set yet")
    where = address or f"{zip_code}, USA"
    if where not in _HOME_SPOTS:
        found = travel.geocode(where)
        if not found:
            raise LookupError(f"couldn't find home ({where}) on the map")
        _HOME_SPOTS[where] = float(found[0]["lat"]), float(found[0]["lon"])
    return *_HOME_SPOTS[where], where


def _nearest(store: str, home: tuple[float, float]) -> dict | None:
    key = store.lower().replace("club", "").strip()
    for query in STORE_SEARCH.get(key, [store]):
        found = _osm_near(query, *home)
        # closest five by straight line, then the shortest real drive
        found.sort(key=lambda p: (float(p["lat"]) - home[0]) ** 2 + ((float(p["lon"]) - home[1]) * 0.8) ** 2)
        found = found[:5]
        if found:
            mins = _drive_minutes(home, found)
            best = found[mins.index(min(mins))]
            return {"store": store, "name": best.get("name") or query, "lat": float(best["lat"]), "lon": float(best["lon"]),
                    "address": _street(best)}
    return None


_US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
    "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma",
    "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
    "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia"}


def _street(place) -> str:
    """'100 Main Street, Decatur' from a Nominatim result — for any US address, not just the Springfield area
    (military families move). Prefers the structured address (addressdetails); falls back to the display name,
    where the town is the part just before the state (skipping a county)."""
    if isinstance(place, dict):
        a = place.get("address") or {}
        street = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
        town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or ""
        if street and town:
            return f"{street}, {town}"
        display = place.get("display_name", "")
    else:
        display = place or ""
    parts = display.split(", ")[1:]                       # drop the place's own name
    if len(parts) >= 2 and parts[0].isdigit():
        parts = [f"{parts[0]} {parts[1]}"] + parts[2:]
    town = ""
    for i, part in enumerate(parts):
        if part in _US_STATES:
            before = [x for x in parts[1:i] if not x.endswith((" County", " Parish", " Borough"))]
            town = before[-1] if before else ""
            break
    return ", ".join(x for x in (parts[0] if parts else "", town) if x)


def _trip_order(home: tuple[float, float], stops: list[dict], roundtrip: bool) -> list[dict]:
    from tools import travel

    coords = ";".join(f"{x[1]},{x[0]}" for x in [home] + [(p["lat"], p["lon"]) for p in stops])
    r = travel._get(f"https://router.project-osrm.org/trip/v1/driving/{coords}?source=first"
                    f"&roundtrip={'true' if roundtrip else 'false'}&overview=false", 20)
    return [stops[i] for i in sorted(range(len(stops)), key=lambda i: r["waypoints"][i + 1]["waypoint_index"])]


@tool(
    "Plan the grocery run: finds the nearest location of each store they need this week (e.g. Commissary, Costco, "
    "Sam's Club, Aldi, Lidl, Walmart, Food Lion), puts them in the fastest driving order from home and back, and "
    "gives the drive time plus one Google Maps link with every stop. Use after deciding which items come from "
    "which store (bulk staples at Costco/Sam's, flyer deals at Aldi/Lidl/Food Lion, the rest at the commissary).",
    {"stores": {"type": "array", "items": {"type": "string"}, "description": "Store names, e.g. ['Costco', 'Aldi', 'Commissary']"},
     "cold_last": {"type": "string", "description": "Optional: the store with most frozen/cold items, visited last"}},
    required=["stores"], slow=True,
)
def shopping_route(stores: list, cold_last: str = "") -> str:
    from tools import travel

    data = _data()
    try:
        lat, lon, where = _home(data["household"])
    except LookupError as e:
        return (f"I can't plan the route yet: {e}. Tell me your home address or ZIP code (it's saved with "
                "set_household_food) and I'll map the stores from there.")
    stops, missing = [], []
    for s in [str(x).strip() for x in stores if str(x).strip()][:8]:
        found = _nearest(s, (lat, lon))
        (stops.append(found) if found else missing.append(s))
    if not stops:
        return "Couldn't find those stores near home on the map."
    last = next((p for p in stops if cold_last and cold_last.lower() in p["store"].lower()), None)
    if last and len(stops) > 1:  # cold store fixed last: best order for the others, then it
        ordered = _trip_order((lat, lon), [p for p in stops if p is not last], roundtrip=False) + [last]
    else:
        ordered = _trip_order((lat, lon), stops, roundtrip=True)
    full = ";".join(f"{x[1]},{x[0]}" for x in [(lat, lon)] + [(p["lat"], p["lon"]) for p in ordered] + [(lat, lon)])
    route = travel._get(f"https://router.project-osrm.org/route/v1/driving/{full}?overview=false", 20)["routes"][0]
    minutes, miles = route["duration"] / 60, route["distance"] / 1609.344
    link = "https://www.google.com/maps/dir/?" + urllib.parse.urlencode({
        "api": "1", "origin": where, "destination": where, "travelmode": "driving",
        "waypoints": "|".join(f"{p['lat']},{p['lon']}" for p in ordered)})
    data["route"] = {"date": date.today().isoformat(), "stops": ordered, "minutes": round(minutes),
                     "miles": round(miles, 1), "link": link}
    storage.save(FILE, data)
    lines = [f"{i}. {p['store']}: {p['name']}, {p['address']}" for i, p in enumerate(ordered, 1)]
    return (f"Shopping route from home ({where}): about {minutes:.0f} min of driving, {miles:.0f} miles round trip "
            "(plus shopping time):\n" + "\n".join(lines) + f"\nOne-tap navigation with every stop: {link}"
            + (f"\nNot found on the map: {', '.join(missing)}." if missing else "")
            + "\nBring a cooler bag; frozen and cold items go in last. It's also on the kitchen page.")


def app_data() -> dict:
    """The phone kitchen page: plan, recipes, shopping items, the person's targets and food this week."""
    data = _data()
    n = storage.load(NUTRITION, {"targets": {}, "log": []})
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    return {"household": data["household"], "plan": data.get("plan", {}), "recipes": data["recipes"],
            "shopping": _shopping_items(data), "targets": n.get("targets", {}),
            "today": [r for r in n["log"] if r["date"] == days[-1]],
            "week": [{"date": d, "kcal": _day_totals(n["log"], d)[0], "protein_g": _day_totals(n["log"], d)[1]} for d in days],
            "toddler_rules": TODDLER_RULES, "route": data.get("route")}
