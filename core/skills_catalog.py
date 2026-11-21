"""The companion's Skills browser: what Jarvis can actually do, read from the live tool registry.

Nothing here is a hand-kept list of skills. Every entry is a registered @tool (tools/registry.py): its name, its
own description, its inputs (the JSON schema the model is given) and the module it lives in. The examples are the
phrases its description itself quotes; its shelf comes from its module; its availability from facts the code
states — a key the module asks for by name and is missing, the service it depends on (registry._service), whether
it is slow. Running one goes through registry.run, exactly as the brain runs it: the same validation, the same
in-tool approvals, the same "not during a game" rule.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import time

# The shelf each tool module sits on. A module missing here is shown under its own name.
SHELVES = {
    "weather": "Weather & travel", "travel": "Weather & travel",
    "kitchen": "Food & kitchen", "pantry": "Food & kitchen",
    "fitness": "Fitness & health", "trainer": "Fitness & health", "pca": "Fitness & health", "pca_health": "Fitness & health", "military": "Military & service", "finance": "Money",
    "life": "Daily life", "today": "Daily life", "proactive": "Daily life",
    "notes": "Notes & memory", "memory": "Notes & memory", "vault": "Notes & memory",
    "fashion": "Wardrobe & style", "studio": "Wardrobe & style", "dressing": "Wardrobe & style",
    "homelab": "Home lab & media", "qbittorrent": "Home lab & media", "movies": "Home lab & media",
    "apps": "PC control", "system": "PC control", "software": "PC control", "computer": "PC control",
    "pcview": "PC control", "media": "PC control",
    "screen": "Screen & vision", "screenread": "Screen & vision", "eyes": "Screen & vision",
    "gaming": "Games", "tutor": "Learning", "languages": "Learning",
    "engineering": "Engineering & projects", "projects": "Engineering & projects", "bench": "Engineering & projects",
    "blender": "3D & Blender", "reach": "Web & research", "voice": "People & devices",
    "interface": "Jarvis himself", "selfinfo": "Jarvis himself",
}
_WORDS = {"pc": "PC", "ai": "AI", "url": "URL", "tv": "TV", "aft": "AFT", "glb": "GLB", "qbit": "qBittorrent",
          "id": "ID", "ip": "IP", "api": "API", "gpu": "GPU", "usb": "USB", "pdf": "PDF", "ocr": "OCR"}
# A phrase the description quotes, the way a person would say it. The opening quote can't follow a letter (that's a
# possessive: "user's"), an apostrophe inside counts only between letters ("what's"), so 'stop', 'skip' stays two.
_QUOTED = re.compile(r"(?<![A-Za-z0-9])[\"'“‘]((?:[A-Za-z0-9 ,.?!\-]|(?<=[A-Za-z])'(?=[A-Za-z])){3,60}?)"
                     r"[\"'”’](?![A-Za-z])")
_GETENV = re.compile(r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]{2,})["']\s*\)""")
_CACHE: dict = {"at": 0.0, "value": None}
CACHE_SECONDS = 60.0


def friendly(name: str) -> str:
    """read_screen -> "Read screen", pc_command -> "PC command"."""
    words = [_WORDS.get(w, w) for w in str(name).split("_") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def summary(description: str, limit: int = 140) -> str:
    """The description's first sentence, trimmed."""
    text = " ".join(str(description or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    first = m.group(1) if m else text
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def examples(description: str, limit: int = 4) -> list[str]:
    """Phrases the tool's own description quotes as things a person says ("show my PC", "read my screen")."""
    out = []
    for m in _QUOTED.finditer(str(description or "")):
        phrase = m.group(1).strip().rstrip(",")
        if (" " not in phrase or not phrase[:1].isalpha() or re.search(r"[\\/{}<>=_]|\.\w{2,4}\b", phrase)
                or not re.search(r"[A-Za-z?!.]$", phrase)):
            continue                                  # a single token, a path, a file name or a code value
        if phrase.lower() not in (p.lower() for p in out):
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


def _module_source(module) -> str:
    try:
        return inspect.getsource(module)
    except (OSError, TypeError):
        return ""


def required_keys(module) -> list[str]:
    """Settings the module asks for by name with no default AND tells the user to add (its own error text names
    it) — e.g. homelab's JELLYFIN_API_KEY. Optional settings with defaults never count."""
    src = _module_source(module)
    keys = []
    for name in sorted(set(_GETENV.findall(src))):
        if re.search(rf"(?:add|set|needs?|missing|create)[^\n\"']{{0,80}}{name}|{name}=", src, re.I):
            keys.append(name)
    return keys


def _status(module_name: str, module, slow: bool) -> tuple[str, str]:
    missing = [k for k in required_keys(module) if not os.getenv(k)]
    if missing:
        return "needs_setup", "needs " + ", ".join(missing)
    try:
        from core import pcbridge
        if slow and pcbridge.local_pc():
            from tools import gaming
            if gaming.game_mode():
                return "paused", "paused during a game"
    except Exception:
        pass
    return "ready", "ready"


def _params(schema: dict) -> list[dict]:
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    out = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        item = spec.get("items") if isinstance(spec.get("items"), dict) else {}
        out.append({"name": name, "type": spec.get("type", "string"), "required": name in required,
                    "description": " ".join(str(spec.get("description", "")).split())[:160],
                    **({"enum": [str(v) for v in spec["enum"]]} if isinstance(spec.get("enum"), list) else {}),
                    **({"items_type": item.get("type")} if item.get("type") else {})})
    out.sort(key=lambda p: (not p["required"], p["name"]))
    return out


def build() -> dict:
    """Every registered skill as the companion shows it."""
    from tools import registry
    skills, shelves = [], set()
    # Status checks and parallel startup may lazily register other modules.
    # Describe one stable snapshot instead of invalidating a live dict iterator.
    for name, entry in list(registry._TOOLS.items()):
        fn = entry["fn"]
        module_name = fn.__module__.replace("tools.", "")
        module = sys.modules.get(fn.__module__)
        schema = entry["schema"]
        desc = " ".join(str(schema.get("description", "")).split())
        shelf = SHELVES.get(module_name, friendly(module_name))
        status, label = _status(module_name, module, bool(entry.get("slow")))
        try:
            depends = registry._service(name)
        except Exception:
            depends = ""
        skills.append({"name": name, "title": friendly(name), "summary": summary(desc), "description": desc,
                       "category": shelf, "module": module_name, "examples": examples(desc),
                       "params": _params(schema.get("input_schema")), "slow": bool(entry.get("slow")),
                       "direct": bool(entry.get("direct")), "status": status, "status_label": label,
                       "depends_on": "" if depends == "the service it needs" else depends})
        shelves.add(shelf)
    skills.sort(key=lambda s: (s["category"], s["title"]))
    return {"count": len(skills), "categories": sorted(shelves), "skills": skills}


def catalog() -> dict:
    """build(), cached for a minute (the registry doesn't change while Jarvis runs; settings rarely do)."""
    now = time.monotonic()
    if _CACHE["value"] is None or now - _CACHE["at"] > CACHE_SECONDS:
        _CACHE["value"], _CACHE["at"] = build(), now
    return _CACHE["value"]


def _flatten(output) -> tuple[str, list[str]]:
    """A tool's result as text plus at most four images (data URLs) for the companion to show."""
    if isinstance(output, str):
        return output, []
    texts, images = [], []
    for block in output if isinstance(output, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(str(block.get("text", "")))
        elif block.get("type") == "image" and len(images) < 4:
            src = block.get("source") or {}
            data = src.get("data", "")
            if src.get("type") == "base64" and isinstance(data, str) and len(data) <= 3_000_000:
                images.append(f"data:{src.get('media_type', 'image/png')};base64,{data}")
    return "\n".join(t for t in texts if t.strip()), images


def run(uid: str, name: str, args: dict) -> dict:
    """Run one skill for `uid` from the companion's menu — through tools/registry.run, as the brain does."""
    from core import pcbridge, progress, users
    from tools import registry
    if name not in registry.names():
        return {"ok": False, "text": f"I don't have a skill called {name}.", "seconds": 0}
    args = registry.coerce(name, args if isinstance(args, dict) else {})
    problem = registry.validate(name, args)
    if problem:
        return {"ok": False, "text": f"Some of the details aren't right: {problem}.", "seconds": 0, "invalid": True}
    started = time.monotonic()
    print(f"  [tool] {name}({args})  <- companion menu")
    progress.start(f"{friendly(name)} (from the companion's menu)")
    try:
        with users.acting_as(uid), pcbridge.user_message(uid, f"(companion menu) run {friendly(name)}"):
            output, is_error = registry.run(name, args)
    finally:
        progress.done()
    text, images = _flatten(output)
    if isinstance(output, str) and output.strip():
        print(f"    -> {'ERROR ' if is_error else ''}{output.strip().splitlines()[0][:160]}")
    return {"ok": not is_error, "text": text[:8000], "images": images,
            "seconds": round(time.monotonic() - started, 1)}


def plain_summary(text: str, limit: int = 320) -> str:
    """When no brain can put a result into words: its opening sentences, as they are."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > 80 else cut.rstrip() + "…")


__all__ = ["SHELVES", "friendly", "summary", "examples", "required_keys", "build", "catalog", "run", "plain_summary"]
