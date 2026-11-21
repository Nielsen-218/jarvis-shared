"""Household pantry + Haitian-style recipes from what you already have.

Flow: phone "eyes" scans a receipt -> OCR (core.pcvision, free, local) -> the local model
parses it into food items -> they go in the pantry. JARVIS then builds Haitian-style recipes
from what's on hand, prioritizing items about to go to waste, grounded in
knowledge/haitian-cuisine.md. All generation runs on the free local model (core.offline).

Shared household data in household/pantry.json (syncs across devices via Syncthing).
"""
import json
import re
import time
import urllib.request
from pathlib import Path

from core import storage
from core.words import plural
from tools.registry import tool

FILE = "household/pantry.json"
KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge" / "haitian-cuisine.md"
# Lines on a receipt that are clearly not groceries to stock.
_NONFOOD = re.compile(r"\b(total|subtotal|tax|change|cash|card|visa|mastercard|debit|balance|"
                      r"tender|auth|ref|store|cashier|receipt|thank you|savings|coupon|loyalty|"
                      r"account|member|phone|www|http|invoice|qty|price|amount)\b", re.I)


def _data() -> dict:
    d = storage.load(FILE, {"items": {}})
    d.setdefault("items", {})
    return d


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "item"


def _local_chat(system: str, user: str, *, json_mode: bool = False, max_tokens: int = 1000, temperature: float = 0.3) -> str:
    """One free local-model completion (Ollama/LM Studio via core.offline). Raises on failure."""
    from core import intelligence, offline
    if intelligence.uses_ollama("fast"):
        # Ollama natively, on the brain itself: its num_ctx (the /v1 endpoint can't set it and reloaded the model) and
        # thinking off (qwen3.5 thinks by default through /v1).
        from core import vision, warmth
        name = intelligence.model("fast")
        body = {"model": name, "stream": False, **warmth.ollama_fields(name, "10m"),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        body["options"].update(temperature=temperature, num_predict=max_tokens)
        if json_mode:
            body["format"] = "json"
        req = urllib.request.Request(vision._base() + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())["message"]["content"]
    body = {"model": offline.model_name(),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(offline.URL + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())["choices"][0]["message"]["content"]


def _knowledge() -> str:
    try:
        return KNOWLEDGE.read_text(encoding="utf-8")
    except OSError:
        return "Haitian cuisine: epis base, diri ak sòs pwa, legim, poul nan sòs, griyo, bouyon, pikliz."


# ---------------------------------------------------------------- pantry storage
def add_items(items: list) -> dict:
    """Add/merge items. Each item: {name, qty?, unit?, use_by?, source?}. Returns the pantry."""
    data = _data()
    now = time.time()
    for it in items:
        name = str(it.get("name", "")).strip()
        if not name:
            continue
        slug = _slug(name)
        prev = data["items"].get(slug, {})
        data["items"][slug] = {
            "name": name,
            "qty": it.get("qty", prev.get("qty", "")),
            "unit": it.get("unit", prev.get("unit", "")),
            "added": prev.get("added", now),
            "use_by": it.get("use_by", prev.get("use_by")),
            "source": it.get("source", prev.get("source", "manual")),
        }
    storage.save(FILE, data)
    return data


def list_items() -> list:
    """Pantry items, oldest first (oldest/soonest-to-spoil are the use-up candidates)."""
    items = list(_data()["items"].values())
    return sorted(items, key=lambda it: (it.get("use_by") or "9999", it.get("added", 0)))


def remove_item(name: str) -> bool:
    data = _data()
    slug = _slug(name)
    key = slug if slug in data["items"] else next((k for k in data["items"] if slug in k), None)
    if key and key in data["items"]:
        del data["items"][key]
        storage.save(FILE, data)
        return True
    return False


def parse_receipt(ocr_text: str) -> list:
    """Turn messy receipt OCR text into grocery items using the free local model.
    Returns [{name, qty}]; drops prices, totals and non-food lines. Falls back to a light
    heuristic if the model is unavailable so a scan is never lost."""
    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
    system = ("You read grocery receipts. Return ONLY JSON: {\"items\":[{\"name\":string,\"qty\":string}]}. "
              "Include only actual food/grocery products, cleaned to plain names (e.g. 'GV WHL MILK 1GAL' -> "
              "'whole milk', qty '1 gal'). Drop totals, tax, store info, prices and non-food. qty may be empty.")
    try:
        raw = _local_chat(system, "RECEIPT:\n" + "\n".join(lines[:120]), json_mode=True, max_tokens=900)
        items = json.loads(raw).get("items", [])
        out = [{"name": str(i.get("name", "")).strip(), "qty": str(i.get("qty", "")).strip()}
               for i in items if str(i.get("name", "")).strip()]
        if out:
            return out
    except Exception:
        pass
    # Heuristic fallback: keep plausible product lines, strip trailing prices.
    out = []
    for ln in lines:
        if _NONFOOD.search(ln) or len(ln) < 3:
            continue
        name = re.sub(r"\s*\$?\d+[.,]\d{2}\b.*$", "", ln).strip()
        name = re.sub(r"\s{2,}.*$", "", name).strip()
        if len(name) >= 3 and re.search(r"[a-zA-Z]{3}", name):
            out.append({"name": name.title(), "qty": ""})
    return out[:60]


# ---------------------------------------------------------------- recipe generation
def recipe_from_pantry(focus: str = "", note: str = "") -> str:
    """Build a Haitian-style recipe from the pantry, prioritizing use-up items (the oldest, or
    `focus` if given). Runs on the free local model, grounded in the cuisine reference."""
    items = list_items()
    if not items and not focus:
        return "Your pantry is empty — scan a receipt or tell me a few things you have, and I'll build a recipe."
    have = ", ".join(f"{it['name']}{(' (' + it['qty'] + ')') if it.get('qty') else ''}" for it in items[:40])
    use_up = focus or ", ".join(it["name"] for it in items[:5])
    system = ("You are a Haitian home cook. Use this reference:\n\n" + _knowledge()[:4000] +
              "\n\nBuild ONE authentic Haitian-style recipe that uses mostly what's on hand, especially the "
              "use-up items, to avoid waste. Family of 5 incl. an 18-month-old: include a toddler-safe note. "
              "Output: dish name (Creole + English), servings, total time, ingredients (mark which are from "
              "the pantry vs to buy), numbered steps, and the toddler version. Keep it budget-first.")
    user = f"On hand: {have}\nUse up first: {use_up}" + (f"\nExtra request: {note}" if note else "")
    try:
        return _local_chat(system, user, max_tokens=1100, temperature=0.5)
    except Exception:
        return ("I couldn't reach the local model to write the recipe. With " + use_up + ", a Haitian go-to is "
                "legim (stew those vegetables with epis over rice) or sòs pwa over diri — want me to try again?")


# ---------------------------------------------------------------- JARVIS voice tools
@tool("Add groceries to the household pantry (things you already have). Give a comma or newline list, "
      "e.g. '2 lb rice, 1 gal milk, 4 plantains, chicken'.",
      {"items": {"type": "string", "description": "comma/newline separated items, optional quantities"}})
def add_to_pantry(items: str) -> str:
    parsed = [{"name": re.sub(r"^\s*[\d.]+\s*(lb|lbs|kg|g|oz|gal|qt|pk|pack|ct|x)?\s*", "", p, flags=re.I).strip() or p.strip(),
               "qty": (re.match(r"^\s*([\d.]+\s*(?:lb|lbs|kg|g|oz|gal|qt|pk|pack|ct|x)?)", p, re.I) or [""])[0].strip(),
               "source": "manual"}
              for p in re.split(r"[\n,]+", items) if p.strip()]
    add_items(parsed)
    return f"Added {plural(len(parsed), 'item')} to the pantry. You now have {plural(len(list_items()), 'item')}."


@tool("List what's in the household pantry (oldest / soonest-to-use first).")
def whats_in_pantry() -> str:
    items = list_items()
    if not items:
        return "The pantry is empty. Scan a receipt or add items to start."
    return "In the pantry: " + "; ".join(
        f"{it['name']}{(' ' + it['qty']) if it.get('qty') else ''}" for it in items[:40])


@tool("Parse a receipt's text into the pantry (use when the receipt text is already read). "
      "The phone's camera scan does this automatically from a photo.",
      {"text": {"type": "string", "description": "the raw text from a receipt"}})
def add_receipt(text: str) -> str:
    items = parse_receipt(text)
    if not items:
        return "I couldn't pick out grocery items from that receipt text."
    add_items([{**it, "source": "receipt"} for it in items])
    return f"Added {plural(len(items), 'item')} from the receipt: " + ", ".join(i["name"] for i in items[:15]) + (
        "…" if len(items) > 15 else "") + f". Pantry now has {plural(len(list_items()), 'item')}."


@tool("Create a Haitian-style recipe from what's in the pantry, using up food before it goes to waste. "
      "Optionally name the item(s) to use up or an extra request (e.g. 'use the eggplant', 'quick dinner').",
      {"focus": {"type": "string", "description": "item(s) to use up, optional"},
       "note": {"type": "string", "description": "extra request, optional"}},
      required=[], slow=True)
def use_up_food(focus: str = "", note: str = "") -> str:
    return recipe_from_pantry(focus, note)
