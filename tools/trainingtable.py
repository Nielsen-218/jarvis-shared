"""The Training Table by voice: the chef and the nutritionist (core/nutri.py, core/chef.py; docs/KITCHEN-V2.md)."""
from core import chef, nutri
from tools.registry import tool


def _find(name: str) -> dict | None:
    did = name if name in nutri.library() else chef.match_dish(name)
    return nutri.dish(did) if did else None


@tool(
    "The chef: what the family can cook RIGHT NOW from the pantry ('what can we cook tonight?', 'make something from "
    "what we have', 'budget is tight, what can I make?'). Dishes from the family's Haitian/Caribbean library first; when "
    "nothing fits, the chef makes up one recipe from exactly what is on hand. Answer only from the result.",
    {"note": {"type": "string", "description": "Anything he added, e.g. 'something quick', 'use the chicken'; '' if none"}},
    required=[], direct=True, slow=True,
)
def chef_cook_now(note: str = "") -> str:
    from tools import pantry
    have = pantry.list_items()
    if not have:
        return "The pantry is empty in my records. Snap a photo of your groceries in the Kitchen tab and I'll cook from that."
    lib = nutri.library()
    meals = nutri.cook_now(have, limit=3)
    ready = [m for m in meals if not m["missing"]]
    if ready:
        names = [lib[m["dish"]]["name"] for m in ready]
        return ("You can make " + (", ".join(names[:-1]) + " or " + names[-1] if len(names) > 1 else names[0])
                + " with what you have. The recipes are in the Kitchen tab.")
    d = chef.make_scraps([p["name"] for p in have] + ([note] if note else []))
    if d:
        first = f" {d['steps'][0]}" if d.get("steps") else ""
        return f"Chef's pick from what's on hand: {d['name']}.{first} The full recipe is in the Kitchen tab."
    if meals:
        m = meals[0]
        return f"You're closest to {lib[m['dish']]['name']}; you'd need {', '.join(m['missing'])}."
    return "I couldn't put a meal together from the pantry right now. Try again in a minute."


@tool(
    "Like or dislike a dish so the nutritionist plans more or less of it ('I love the poul nan sòs', 'no more legim', "
    "'take griot off'). Disliked dishes are never planned again.",
    {"dish": {"type": "string"}, "like": {"type": "boolean", "description": "true = like, false = dislike"}},
    direct=True,
)
def rate_dish(dish: str, like: bool) -> str:
    d = _find(dish)
    if not d:
        return f"I don't have '{dish}' in the family's dish list."
    nutri.vote(d["id"], "like" if like else "dislike")
    return f"Got it: {d['name']} {'goes on your favourites' if like else 'is off the plan'}."


@tool(
    "Log a dish he ate so it counts toward today's calories and protein ('I had griot for lunch', 'log the akasan'). "
    "Use log_food instead for food that isn't a family dish.",
    {"dish": {"type": "string"}, "slot": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
     "portion": {"type": "number", "description": "Portions eaten, 1 = one adult plate"}},
    required=["dish", "slot"], direct=True,
)
def log_dish(dish: str, slot: str, portion: float = 1.0) -> str:
    d = _find(dish)
    if not d:
        return f"I don't have '{dish}' in the dish list; tell me what was in it and I'll log it as food instead."
    m = nutri.eat(d["id"], slot, portion or 1.0)
    e, t = nutri.eaten_today(), nutri.targets()
    left = f" You have {max(0, t['protein_g'] - e['protein_g'])} g of protein to go today." if t else ""
    return f"Logged {d['name']}: {m['kcal']} calories, {m['protein_g']} g protein.{left}"


@tool(
    "Today at the Training Table: the day type from the trainer, his calorie and protein targets, what he has eaten and "
    "the planned meals ('what am I eating today?', 'how's my protein?', 'what's for dinner?').",
    direct=True,
)
def training_table_today() -> str:
    kind, label = nutri.day_type()
    t, e = nutri.targets(), nutri.eaten_today()
    lib = nutri.library()
    today = next((d for d in nutri.week() if d["date"] == nutri.date.today().isoformat()), None)
    meals = "; ".join(f"{s}: {lib[m]['name']}" for s, m in (today or {}).get("meals", {}).items() if m in lib)
    head = f"{label}." if label else ""
    if not t:
        return f"{head} Tell me your weight and I'll set your targets. Planned: {meals or 'nothing yet'}."
    return (f"{head} Target {t['kcal']} calories and {t['protein_g']} g protein; so far {e['kcal']} and "
            f"{e['protein_g']} g. Planned: {meals or 'nothing yet'}.")


@tool(
    "Record a weigh-in in pounds ('I weigh 192', 'weighed in at 188 this morning'); the nutritionist's targets follow it.",
    {"lbs": {"type": "number"}}, direct=True,
)
def weigh_in(lbs: float) -> str:
    try:
        nutri.log_weight(lbs)
    except ValueError:
        return "That weight doesn't look right; say it in pounds."
    t = nutri.targets()
    return f"Logged {lbs:g} lb. Today's targets: {t['kcal']} calories, {t['protein_g']} g protein." if t else f"Logged {lbs:g} lb."
