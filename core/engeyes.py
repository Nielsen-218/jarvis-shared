"""Engineering Eyes (Issue #9): identify hardware in a still photo and guide a build one checked step at a time.

Runs locally on the GPU PC, $0:
  * RapidOCR reads printed markings (chip numbers, module labels) — grounding for the identification.
  * A local vision model in Ollama (Qwen3-VL, Apache-2.0) names the item, says how sure it is and why,
    and returns boxes (0-1000 relative) so the phone can label the photo.
  * A deterministic safety layer — code, not a prompt — refuses wire-level guidance for mains and
    high-voltage work, and flags lithium cells, hot irons, power tools.

Nothing here sees live video: every answer is about the still photo that was sent, and a project step is
only marked done from a NEW photo that shows it. Photos are not stored; only the project's text state is
kept per person (eng_project.json).
"""
import base64
import io
import json
import os
import re
import time
import urllib.request

from core.words import plural

# Best first. Since 2026-11-09 the eyes are the one brain (qwen3.5:9b sees: ScreenSpot Pro 65.2, above qwen3-vl);
# the older vision builds are only used if the brain is missing and one of them is still installed.
PREFERRED_MODELS = ("qwen3.5:9b", "qwen3-vl:8b-instruct", "qwen2.5vl:7b", "llava:7b")
MAX_STEPS = 8
_WORK = re.compile(r"\b(wire|wiring|connect|hook(ing)? up|solder|repair|fix|open|replace|install|rewire|"
                   r"splice|strip|mount|build|swap|modify)\b", re.I)


class EngError(RuntimeError):
    """A clean, user-facing failure (no image, no project, model unavailable)."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# -- safety ----------------------------------------------------------------------------------------
_STOP = [
    (re.compile(r"\b(mains|wall (outlet|socket|wiring)|(ac|power|electrical) outlet|receptacle|breaker( box| panel)?|"
                r"fuse ?box|consumer unit|electrical panel|light switch|ceiling (light|fan)|hard-?wir\w*|"
                r"live wire|line voltage|240v? (plug|socket)|thermostat wiring)\b", re.I), "household mains wiring"),
    (re.compile(r"\b(1[012]0|2[0-4]0|208|277|347|480)\s*-?\s*v(olts?|ac)?\b|\b\d+\s*-\s*2[0-4]0\s*v", re.I), "mains voltage"),
    (re.compile(r"\b(high[- ]voltage|\d+(\.\d+)?\s*kv|kilovolts?|flyback|crt|cathode ray|neon sign|tesla coil|"
                r"ignition coil|stun gun|taser|microwave( oven| transformer)?|capacitor bank|defibrillator)\b", re.I),
     "high voltage"),
    (re.compile(r"\b(ev|hybrid|electric vehicle|e-?bike|electric scooter)\s+(battery|pack)\b", re.I), "a high-voltage battery pack"),
    (re.compile(r"\b(inside|open(ing)?|repair(ing)?|fix(ing)?)\s+(a |an |the |my )?(power supply|psu|atx supply|tv|"
                r"television|monitor|microwave|charger)\b", re.I), "the inside of a mains-powered device"),
    # The classic hobby trap: a relay board switching a household appliance is mains work.
    # (Not "fan"/"pump": 12 V DC fans and pumps are everyday safe builds; mains cues still catch them.)
    (re.compile(r"\brelay\b.{0,60}\b(lamp|light ?bulbs?|bulb|heater|kettle|appliance|outlet|wall plug|mains|ac load|"
                r"coffee maker|toaster|household)\b|\b(lamp|appliance|outlet|heater|kettle)\b.{0,60}\brelay\b", re.I),
     "a relay switching a mains appliance"),
]
_CAUTION = [
    (re.compile(r"\b(li-?po|li-?ion|lithium|18650|21700|battery pack)\b", re.I),
     "lithium cells can catch fire if shorted, punctured or charged wrong — never short the terminals"),
    (re.compile(r"\bsolder(ing)?\b", re.I), "a soldering iron tip runs around 350 °C — use a stand and work ventilated"),
    (re.compile(r"\b(drill|saw|grinder|dremel|rotary tool|router|angle grinder)\b", re.I),
     "power tools — eye protection, and clamp the work"),
    (re.compile(r"\b(car battery|12\s*v lead|lead[- ]acid)\b", re.I),
     "a car battery can dump hundreds of amps into a short — remove rings and insulate tools"),
    (re.compile(r"\b(2[5-9]|[3-4]\d)\s*v\b", re.I), "it's above 24 V — double-check polarity and insulation"),
]
STOP_ADVICE = ("I can't safely judge live mains or high-voltage work from a photo, so I won't give wire-by-wire steps. "
               "Switch it off at the breaker or unplug it, confirm it's dead with a non-contact tester, and have a "
               "licensed electrician do the wiring.")


def assess(*texts, doing_work=True) -> dict:
    """Deterministic hazard check. `doing_work=False` (just identifying) downgrades mains to a note."""
    blob = " ".join(t for t in texts if t)
    stop = [label for rx, label in _STOP if rx.search(blob)]
    caution = [label for rx, label in _CAUTION if rx.search(blob)]
    if stop and doing_work:
        return {"level": "stop", "reasons": sorted(set(stop)),
                "message": f"Safety first: this involves {sorted(set(stop))[0]}. {STOP_ADVICE}"}
    if stop:  # looking at a charger, a monitor, a microwave: fine to identify, not to open up
        caution.insert(0, "it's mains-powered — don't open it or work on it while it's plugged in")
    if caution:
        return {"level": "caution", "reasons": caution, "message": "Careful: " + "; ".join(dict.fromkeys(caution)) + "."}
    return {"level": "none", "reasons": [], "message": ""}


# -- local models ----------------------------------------------------------------------------------
def _ollama_base() -> str:
    from core import vision
    return vision._base()


def model() -> str | None:
    """Best installed vision model (JARVIS_ENG_VISION_MODEL wins), or None."""
    try:
        with urllib.request.urlopen(_ollama_base() + "/api/tags", timeout=4) as r:
            names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
    except Exception:
        return None
    try:
        from core import intelligence
        brain = intelligence.vision_model()
    except Exception:  # noqa: BLE001
        brain = None
    for want in (os.getenv("JARVIS_ENG_VISION_MODEL"), brain, *PREFERRED_MODELS):
        if want and want in names:
            return want
    return None


def warm() -> None:
    """Load the vision model in the background (a cold load takes ~30 s; framing a shot takes about that)."""
    import threading

    def load():
        name = model()
        if not name:
            return
        try:
            from core import warmth
            req = urllib.request.Request(_ollama_base() + "/api/generate", data=json.dumps(
                {"model": name, "prompt": "", **warmth.ollama_fields(name, "10m")}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=120).read()
        except Exception:
            pass  # the real request will load it anyway
    threading.Thread(target=load, daemon=True, name="engeyes-warm").start()


def _b64(image: str) -> str:
    return image.split(";base64,", 1)[1] if image.startswith("data:") and ";base64," in image else image


def _decode(image: str):
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(base64.b64decode(_b64(image))))
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise EngError("That photo couldn't be read. Try taking it again.") from None


def _vlm(images: list, prompt: str, schema: dict, system: str = "") -> dict:
    """One structured answer from the local vision model. Raises EngError if it's not available."""
    name = model()
    if not name:
        raise EngError("Engineering Eyes needs the local vision model on the PC, and it isn't running. "
                       "Make sure Jarvis and Ollama are up on the PC.", 503)
    from core import warmth
    # Ollama drops the "format" schema when thinking is off (qwen3.5, ollama#14645), so the shape is in the prompt too.
    prompt = f"{prompt}\n\nReply with JSON only, matching this JSON schema: {json.dumps(schema, separators=(',', ':'))}"
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt, **({"images": [_b64(i) for i in images]} if images else {})}]
    body = {"model": name, "messages": messages, "stream": False, "format": schema, **warmth.ollama_fields(name, "10m")}
    body["options"]["temperature"] = 0  # deterministic: boxes shouldn't wander between runs; num_ctx = the brain's
    req = urllib.request.Request(_ollama_base() + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            content = json.loads(r.read().decode())["message"]["content"]
        found = json.loads(content[content.find("{"): content.rfind("}") + 1])
        if not isinstance(found, dict):
            raise ValueError("not a JSON object")
        return found
    except Exception as error:  # keep the cause in the log; the phone gets a clean message
        print(f"[engeyes] {name} failed: {type(error).__name__}: {str(error)[:200]}")
        raise EngError("The local vision model didn't give a usable answer. Try again in a moment.", 503) from None


def read_markings(img) -> list:
    """Printed text on the item (chip part numbers, module labels), boxes normalized 0-1."""
    from core import pcvision
    import numpy as np
    try:
        result, _ = pcvision._get_ocr()(np.array(img))
    except Exception:
        return []
    w, h = img.size
    out = []
    for box, text, conf in (result or []):
        text = (text or "").strip()
        if len(text) < 2 or float(conf) < 0.5 or pcvision._SECRET.search(text):
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append({"text": text, "confidence": round(float(conf), 2),
                    "box": [round(min(xs) / w, 4), round(min(ys) / h, 4), round(max(xs) / w, 4), round(max(ys) / h, 4)]})
    return sorted(out, key=lambda m: -m["confidence"])[:25]


def _box(raw, size) -> list | None:
    """A model box ([x1,y1,x2,y2], 0-1000 relative — or pixels from some models) -> 0-1, or None."""
    try:
        x1, y1, x2, y2 = (float(v) for v in raw[:4])
    except Exception:
        return None
    w, h = size
    scale = (w, h) if max(x1, y1, x2, y2) > 1000 else (1000.0, 1000.0)
    b = [x1 / scale[0], y1 / scale[1], x2 / scale[0], y2 / scale[1]]
    b = [round(min(1.0, max(0.0, v)), 4) for v in b]
    if b[2] - b[0] < 0.01 or b[3] - b[1] < 0.01:
        return None
    return b


# -- identify ("What am I holding?") ---------------------------------------------------------------
_BOX = {"type": "array", "items": {"type": "number"}}
IDENTIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string"},
        "category": {"type": "string", "enum": ["component", "module", "board", "sensor", "connector", "cable",
                                                "tool", "fastener", "device", "other"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "what_it_does": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "box_2d": _BOX,
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "box_2d": _BOX}, "required": ["label", "box_2d"]}},
        "uncertain_about": {"type": "string"},
        "better_photo": {"type": "string"},
    },
    "required": ["item", "category", "confidence", "what_it_does", "evidence", "box_2d"],
}


def identify(image: str, question: str = "") -> dict:
    img = _decode(image)
    marks = read_markings(img)
    printed = "; ".join(m["text"] for m in marks[:15]) or "none found"
    prompt = (
        "This is ONE still photo from the owner's phone or webcam (not live video). Identify the main item they are "
        "holding or showing, as an experienced electronics engineer would.\n"
        f"Printed markings read by OCR (may contain errors; part numbers are strong evidence): {printed}.\n"
        f"Their question: {question.strip() or 'What am I holding?'}\n"
        "Rules: name the exact part/model only if the markings or distinctive features support it; otherwise give "
        "the general type and set confidence to medium or low. 'category': board = microcontroller or dev board, "
        "single-board computer or bare PCB; module = small breakout/driver board; sensor = the sensing element or "
        "a sensor breakout; component = a discrete part (resistor, IC, LED, capacitor, transistor). "
        "'what_it_does' = ONE complete sentence starting with 'It', e.g. 'It's a microcontroller board you program "
        "over USB to read sensors and drive LEDs and motors.' 'evidence' = the visible cues you relied on. "
        "'box_2d' = [x1,y1,x2,y2] around the item, each 0-1000 relative to the image width/height. 'parts' = up to "
        "5 notable features worth labeling (chip, pins, ports, connectors) with their boxes — ONLY ones you can "
        "clearly see and are sure of; leave a feature out rather than guess its name. 'uncertain_about' = what "
        "you can't tell from this photo (or empty). 'better_photo' = how to retake it if confidence isn't high "
        "(or empty). Never invent a part number.")
    data = _vlm([image], prompt, IDENTIFY_SCHEMA,
                system="You are Jarvis's engineering eyes. Be precise, honest about uncertainty, and brief.")
    conf = data.get("confidence") if data.get("confidence") in ("high", "medium", "low") else "low"
    item = (data.get("item") or "something I can't make out").strip()
    parts = [{"label": str(p.get("label", ""))[:40], "box": _box(p.get("box_2d"), img.size)}
             for p in (data.get("parts") or [])[:5] if isinstance(p, dict)]
    result = {
        "item": item[:120], "category": data.get("category", "other"), "confidence": conf,
        "what_it_does": _sentence(data.get("what_it_does", ""))[:300],
        "evidence": [str(e)[:120] for e in (data.get("evidence") or [])][:6],
        "uncertain_about": str(data.get("uncertain_about") or "")[:200],
        "better_photo": str(data.get("better_photo") or "")[:200],
        "box": _box(data.get("box_2d"), img.size),
        "parts": [p for p in parts if p["box"]],
        "markings": marks[:10], "size": list(img.size),
    }
    result["safety"] = assess(question, item, result["what_it_does"], printed,
                              doing_work=bool(_WORK.search(question or "")))
    result["reply"] = _say_identify(result)
    return result


def _say_identify(r: dict) -> str:
    lead = {"high": f"That's {_a(r['item'])}.", "medium": f"That looks like {_a(r['item'])}.",
            "low": f"I'm not certain — it might be {_a(r['item'])}."}[r["confidence"]]
    out = [lead, r["what_it_does"]]
    if r["confidence"] != "high" and r["uncertain_about"]:
        out.append(f"I can't tell {r['uncertain_about'].rstrip('.')} from this photo.")
    if r["confidence"] != "high" and r["better_photo"]:
        out.append(r["better_photo"].rstrip(".") + ".")
    if r["safety"]["message"]:
        out.append(r["safety"]["message"])
    return " ".join(s.strip() for s in out if s and s.strip())


def _sentence(text) -> str:
    """'microcontroller development board' -> "It's a microcontroller development board." (speakable)."""
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return ""
    if not re.match(r"^(it|this|that|these|they)\b", s, re.I) and not re.search(r"\b(is|are|lets|let|used|reads|"
                                                                               r"drives|converts|measures|connects)\b", s, re.I):
        s = "It's " + _a(s[0].lower() + s[1:])
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?" else s + "."


def _a(noun: str) -> str:
    noun = noun.strip()
    if re.match(r"^(a|an|the|some|two|three|four|no|its|their|his|her|\d)\b", noun, re.I):
        return noun
    return ("an " if noun[:1].lower() in "aeiou" else "a ") + noun


def annotate(image: str, boxes: list):
    """The photo with labeled boxes drawn on it (for the PC HUD; the phone draws its own overlay).
    boxes: [{"label": str, "box": [x0,y0,x1,y1] 0-1, "main": bool}]."""
    from PIL import ImageDraw, ImageFont
    img = _decode(image)
    img.thumbnail((1280, 1280))
    w, h = img.size
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("segoeui.ttf", max(14, w // 45))
    except OSError:
        font = ImageFont.load_default()
    for b in boxes:
        if not b.get("box"):
            continue
        x0, y0, x1, y1 = b["box"][0] * w, b["box"][1] * h, b["box"][2] * w, b["box"][3] * h
        color = (56, 240, 255) if b.get("main") else (255, 92, 240)
        draw.rectangle([x0, y0, x1, y1], outline=color, width=max(2, w // 300))
        label = b.get("label", "")
        if label:
            tw, th = draw.textbbox((0, 0), label, font=font)[2:]
            ty = y0 - th - 6 if y0 - th - 6 > 0 else y0 + 2
            draw.rectangle([x0, ty, x0 + tw + 10, ty + th + 6], fill=(5, 8, 14))
            draw.text((x0 + 5, ty + 2), label, fill=color, font=font)
    return img


def boxes_of(result: dict) -> list:
    """The labeled boxes worth drawing for an identify result or a step check."""
    if "item" in result:
        return ([{"label": result["item"], "box": result.get("box"), "main": True}]
                + [{"label": p["label"], "box": p["box"]} for p in result.get("parts", [])])
    issue = result.get("issue")
    return [{"label": issue["label"], "box": issue["box"], "main": True}] if issue else []


# -- guided projects ---------------------------------------------------------------------------------
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "instruction": {"type": "string"},
            "check": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "instruction", "check"]}},
    },
    "required": ["title", "parts", "steps"],
}
# The verdict is computed from per-requirement marks in code, not taken from the model: a small model's
# free-form "status" contradicted its own feedback, and it must never guess "done".
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {"type": "array", "items": {"type": "object", "properties": {
            # Field order is generation order: observe ("note") BEFORE deciding ("met"), or a small model commits
            # to a verdict first and then describes something that contradicts it.
            "requirement": {"type": "string"}, "note": {"type": "string"},
            "met": {"type": "string", "enum": ["yes", "no", "unclear"]}, "evidence_box_2d": _BOX},
            "required": ["requirement", "note", "met"]}},
        "issue": {"type": "object", "properties": {"label": {"type": "string"}, "box_2d": _BOX}},
    },
    "required": ["requirements"],
}
# Second look: every "yes" must survive a zoomed close-up of the spot the model pointed at. On a whole
# photo the 8B model called a resistor an LED; cropped and enlarged, small models are far more accurate.
CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"seen": {"type": "string"}, "met": {"type": "string", "enum": ["yes", "no", "unclear"]}},
    "required": ["seen", "met"],
}


def _crop_b64(img, box, pad=0.35, min_side=448) -> str:
    """The boxed region (0-1) with some context, enlarged, as a JPEG data URL."""
    from PIL import Image
    w, h = img.size
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    x0, y0 = max(0.0, x0 - bw * pad), max(0.0, y0 - bh * pad)
    x1, y1 = min(1.0, x1 + bw * pad), min(1.0, y1 + bh * pad)
    crop = img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
    scale = max(1.0, min_side / max(1, min(crop.size)))
    if scale > 1:
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _confirm(img, box, requirement: str) -> dict:
    """Zoom in on the evidence and ask again. Anything but a clear yes downgrades the requirement."""
    data = _vlm([_crop_b64(img, box)],
                "This is a close-up cut from a photo of someone's electronics project. Does this close-up clearly show "
                f"the following? \"{requirement}\"\nFirst, 'seen' = what this close-up actually shows, in a few words. "
                "Then met = 'yes' if what you saw matches it; 'no' only if you saw something that contradicts it (a "
                "different part, a missing wire); 'unclear' if it's too blurry or cut off to tell.", CONFIRM_SCHEMA,
                system="You are a careful electronics inspector. Never assume; describe only what is visible.")
    met = data.get("met") if data.get("met") in ("yes", "no", "unclear") else "unclear"
    return {"met": met, "seen": " ".join(str(data.get("seen", "")).split())[:160]}
PLANNER_SYSTEM = ("You are a patient, exact electronics mentor. Get pin names right: Arduino ground pins are labeled "
                  "GND (never a pin number); digital pins are D0-D13 (0/1 are serial RX/TX); analog are A0-A5. "
                  "One small, photo-checkable step at a time. Low voltage only.")


def _load_project():
    from core import storage
    return storage.load("eng_project.json", None)


def _save_project(p):
    from core import storage
    storage.save("eng_project.json", p)


def _clear_project():
    from core import storage
    p = storage.path("eng_project.json")
    p.unlink(missing_ok=True)


def view(p) -> dict | None:
    """What the phone shows for the current project."""
    if not p:
        return None
    total = len(p["steps"])
    idx = min(p["index"], total)
    step = p["steps"][idx] if idx < total else None
    return {"title": p["title"], "goal": p["goal"], "parts": p.get("parts", []), "index": idx, "total": total,
            "step": step, "finished": idx >= total, "safety": p["safety"], "source": p.get("source", "local"),
            "last": p["history"][-1] if p.get("history") else None}


SAFE_PLAN = {"title": "Safety first", "parts": [], "steps": [
    {"title": "Make it safe", "instruction": "Switch the circuit off at the breaker or unplug the device, and tell "
     "anyone nearby you're working on it.", "check": ["The breaker is switched off or the plug is pulled out"]},
    {"title": "Prove it's dead", "instruction": "Test the work area with a non-contact voltage tester you've just "
     "checked on a known-live outlet.", "check": ["A voltage tester is held at the work area"]},
    {"title": "Hand the wiring to a pro", "instruction": "This is mains or high-voltage work: a licensed electrician "
     "should do the wiring. I can help you describe the job or choose parts.", "check": ["Nothing to check — you're done"]},
]}
SOURCE_NOTE = {"verified": "Verified wiring.",
               "cloud": "Double-check each pin against the labels printed on your board.",
               "local": "Double-check each pin against the labels printed on your board."}


def _norm_steps(raw) -> list:
    steps = []
    for s in raw or []:
        if not isinstance(s, dict) or not s.get("instruction"):
            continue
        check = s.get("check")
        check = [check] if isinstance(check, str) else [str(c) for c in (check or []) if c]
        steps.append({"title": " ".join(str(s.get("title", "")).split())[:80] or f"Step {len(steps) + 1}",
                      "instruction": " ".join(str(s["instruction"]).split())[:500],
                      "check": [" ".join(c.split())[:160] for c in check][:3] or ["The step is finished as described"]})
    return steps[:MAX_STEPS]


def _plan_prompt(goal: str, context: str) -> str:
    return (f"The owner's goal: {goal}\n" + (f"Their parts (from a photo): {context}.\n" if context else "")
            + f"Write a hands-on build plan of 3 to {MAX_STEPS} steps for low-voltage hobby electronics. Step 1 unplugs "
            "the board; the last step powers it on and tests it. Each step is small enough to verify from ONE still "
            "photo. 'instruction' = 1-3 short sentences with exact pin-to-pin wiring (say when they should confirm on "
            "the board's labels or datasheet). 'check' = 1-3 short things a photo of the finished step must show — "
            "visible facts like 'a wire runs from VCC to 5V', never motion like 'blinking'. 'parts' = everything "
            "they need. Never include mains wiring.")


def _cloud_plan(goal: str, context: str) -> dict | None:
    """The strongest free planner (Gemini, if configured). Text only: no photo leaves the house."""
    from core import cloudmodel
    if not cloudmodel.available() or cloudmodel.cooling_down():
        return None
    payload = {"role": "advanced", "messages": [{"role": "system", "content": PLANNER_SYSTEM},
                                                {"role": "user", "content": _plan_prompt(goal, context)}],
               "response_format": {"type": "json_schema", "json_schema": {"name": "build_plan", "schema": PLAN_SCHEMA}}}
    try:
        content = cloudmodel.complete(payload)["choices"][0]["message"]["content"]
        return json.loads(content[content.find("{"): content.rfind("}") + 1])
    except Exception as error:
        print(f"[engeyes] cloud planner unavailable: {type(error).__name__}: {str(error)[:120]}")
        return None


def _with_caution(safety: dict, reason: str) -> dict:
    reasons = list(dict.fromkeys(safety.get("reasons", []) + [reason]))
    if safety.get("level") == "stop":
        return safety
    return {"level": "caution", "reasons": reasons, "message": "Careful: " + "; ".join(reasons) + "."}


def start_project(goal: str, image: str | None = None) -> dict:
    import copy

    from core import engrecipes

    goal = (goal or "").strip()[:300]
    if not goal:
        raise EngError("Tell me what you want to build or connect first.")
    seen = identify(image, goal) if image else None
    context = f"{seen['item']} ({'; '.join(m['text'] for m in seen['markings'][:6])})" if seen else ""
    safety = assess(goal, context)
    recipe = None if safety["level"] == "stop" else engrecipes.match(goal, seen["item"] if seen else "")
    if safety["level"] == "stop":
        plan, source = copy.deepcopy(SAFE_PLAN), "safety"
    elif recipe:
        plan = {"title": recipe["title"], "parts": list(recipe["parts"]), "steps": copy.deepcopy(recipe["steps"])}
        source = "verified"
        if recipe.get("caution"):
            safety = _with_caution(safety, recipe["caution"])
    else:
        raw, source = _cloud_plan(goal, context), "cloud"
        if raw is None:
            raw, source = _vlm([image] if image else [], _plan_prompt(goal, context), PLAN_SCHEMA,
                               system=PLANNER_SYSTEM), "local"
        steps = _norm_steps(raw.get("steps"))
        if not steps:
            raise EngError("I couldn't make a step-by-step plan for that. Try naming the parts and the goal.", 503)
        # A model's own wording can still drift into dangerous territory: re-check the whole plan.
        planned = assess(goal, context, " ".join(s["instruction"] for s in steps))
        if planned["level"] == "stop":
            plan, source, safety = copy.deepcopy(SAFE_PLAN), "safety", planned
        else:
            safety = planned if planned["level"] != "none" else safety
            plan = {"title": " ".join(str(raw.get("title") or goal).split())[:80],
                    "parts": [str(x)[:60] for x in (raw.get("parts") or [])][:12], "steps": steps}
    project = {"goal": goal, "title": plan["title"], "parts": plan["parts"], "steps": plan["steps"], "index": 0,
               "history": [], "safety": safety, "source": source, "created": time.time()}
    _save_project(project)
    first = project["steps"][0]
    note = SOURCE_NOTE.get(source, "")
    reply = (f"{safety['message']} " if safety["level"] != "none" else "") + \
            f"Here's the plan — {plural(len(project['steps']), 'step')}. {note} Step 1, {first['title']}: {first['instruction']} " \
            "When it's done, take a photo and I'll check it."
    return {"project": view(project), "reply": " ".join(reply.split()), "seen": seen}


def check_step(image: str) -> dict:
    p = _load_project()
    if not p:
        raise EngError("There's no project going. Tell me what you want to build first.")
    if p["index"] >= len(p["steps"]):
        return {"project": view(p), "reply": "That project's finished. Start a new one any time."}
    img = _decode(image)
    step = p["steps"][p["index"]]
    reqs = step["check"] if isinstance(step["check"], list) else [step["check"]]
    prompt = (f"Step {p['index'] + 1} of {len(p['steps'])} of '{p['title']}': {step['title']}.\n"
              f"What they were told to do: {step['instruction']}\n"
              "Judge ONLY this still photo against each requirement below. For each one: met = 'yes' only if the photo "
              "clearly shows it; 'no' if the photo shows it missing or wrong; 'unclear' if that area isn't visible, is "
              "blurry or is cut off. note = what you actually see there, in a few words. For every 'yes', give "
              "evidence_box_2d [x1,y1,x2,y2] (0-1000 relative) around exactly where the photo shows it. If there's one "
              "main problem spot, give 'issue' with a short label and its box_2d.\nRequirements:\n"
              + "\n".join(f"{k + 1}. {r}" for k, r in enumerate(reqs)))
    data = _vlm([image], prompt, CHECK_SCHEMA, system="You are a careful electronics mentor checking a student's work.")
    marks = [m for m in (data.get("requirements") or []) if isinstance(m, dict)]
    results = []
    for k, req in enumerate(reqs):  # by position; a missing mark counts as unclear, never as yes
        m = marks[k] if k < len(marks) else {}
        met = m.get("met") if m.get("met") in ("yes", "no", "unclear") else "unclear"
        result = {"requirement": req, "met": met, "note": " ".join(str(m.get("note", "")).split())[:160]}
        if met == "yes":  # a "yes" has to point at its evidence and survive a zoomed second look
            box = _box(m.get("evidence_box_2d"), img.size)
            second = _confirm(img, box, req) if box else {"met": "unclear", "seen": "it couldn't point to where"}
            if second["met"] != "yes":  # the two looks disagree: that's uncertainty, never a "done"
                seen = _lower_first(_first_sentence(second["seen"])) if second["seen"] else ""
                result.update(met="unclear", note=f"in the close-up I see {_a(seen) if seen else 'something else'}")
            result["box"] = box
        results.append(result)
    status = ("not_yet" if any(r["met"] == "no" for r in results)
              else "cant_tell" if any(r["met"] == "unclear" for r in results) else "done")
    issue = data.get("issue") if isinstance(data.get("issue"), dict) else None
    issue = {"label": str(issue.get("label", ""))[:40], "box": _box(issue.get("box_2d"), img.size)} if issue else None
    issue = issue if issue and issue["box"] and status != "done" else None
    p["history"].append({"step": p["index"], "status": status, "results": results, "at": time.time(), "issue": issue})
    if status == "done":
        p["index"] += 1
    _save_project(p)
    if status == "done" and p["index"] >= len(p["steps"]):
        reply = f"That looks right — and that was the last step. {p['title']} is done. Nice work."
    elif status == "done":
        nxt = p["steps"][p["index"]]
        reply = f"That looks right. Step {p['index'] + 1}, {nxt['title']}: {nxt['instruction']}"
    elif status == "not_yet":
        wrong = [r for r in results if r["met"] == "no"]
        reply = "Not quite yet: " + "; ".join(
            _lower_first(_first_sentence(r["note"])) if r["note"] else f"I don't see that {_lower_first(_clause(r['requirement']))}"
            for r in wrong) + ". Fix that and send another photo."
    else:
        unsure = [r for r in results if r["met"] == "unclear"]
        seen = f" — {_clause(unsure[0]['note'])}" if unsure[0]["note"] else ""
        reply = (f"I can't confirm that {_lower_first(_clause(unsure[0]['requirement']))}{seen}. Get closer, light it "
                 "well, keep that part in frame, and send another photo.")
    return {"project": view(p), "reply": " ".join(reply.split()), "verdict": status, "results": results,
            "issue": issue, "size": list(img.size)}


def _clause(text: str) -> str:
    return text.strip().rstrip(".")


def _first_sentence(text: str) -> str:
    """The model's note, trimmed to its first sentence and without a trailing period."""
    text = " ".join(str(text).split())
    return _clause(re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0])


def _lower_first(text: str) -> str:
    """'An LED is…' -> 'an LED is…' (keeps acronyms like 'LED', 'GND', 'USB')."""
    head, _, rest = text.partition(" ")
    keep = (len(head) > 1 and head.isupper()) or head in ("I", "I'm", "I've", "I'd")  # acronyms; the pronoun
    return (head if keep else head[:1].lower() + head[1:]) + (" " + rest if rest else "")


def control(action: str) -> dict:
    p = _load_project()
    if action == "stop":
        _clear_project()
        return {"project": None, "reply": "Project closed."}
    if not p:
        return {"project": None, "reply": "There's no project going."}
    if action == "back":
        p["index"] = max(0, p["index"] - 1)
    elif action == "skip":   # the owner's call; recorded as unverified rather than checked
        if p["index"] < len(p["steps"]):
            p["history"].append({"step": p["index"], "status": "skipped", "results": [], "at": time.time(),
                                 "issue": None, "note": "Skipped without a photo check."})
            p["index"] += 1
    _save_project(p)
    v = view(p)
    if v["finished"]:
        return {"project": v, "reply": "That was the last step."}
    return {"project": v, "reply": f"Step {v['index'] + 1}, {v['step']['title']}: {v['step']['instruction']}"}


def handle(body: dict) -> dict:
    """One entry point for the phone/PC API: {action, image?, question?, goal?}."""
    action = str(body.get("action", ""))
    image = body.get("image") if isinstance(body.get("image"), str) else None
    if image and not image.startswith("data:image/"):
        raise EngError("Send the photo as an image.")
    if action == "identify":
        if not image:
            raise EngError("Take a photo first — I only see still photos you send, not live video.")
        return identify(image, str(body.get("question", ""))[:300])
    if action == "start":
        return start_project(str(body.get("goal", "")), image)
    if action == "check":
        if not image:
            raise EngError("Take a photo of your work so I can check this step.")
        return check_step(image)
    if action in ("back", "skip", "stop"):
        return control(action)
    if action == "status":  # the phone asks this when Engineer mode opens: start loading the model now
        warm()
        p = _load_project()
        return {"project": view(p), "reply": ""}
    raise EngError("Unknown action.")
