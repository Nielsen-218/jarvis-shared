"""Find clickable icons in a screenshot: Microsoft OmniParser v2's icon detector (YOLO, trained on UI screenshots), exported
once to ONNX (models/omniparser-icon-detect-v2-1280.onnx, from huggingface.co/microsoft/OmniParser-v2.0 icon_detect; the
weights inherit Ultralytics' AGPL licence, used here privately). Runs on onnxruntime CPU in ~0.3 s for a 1920x1080 screen,
so it costs no graphics memory and no extra Python packages.

This is the fallback for windows whose controls UI Automation cannot see (Blender, games, launchers): OCR already gives the
words on screen; this adds the icon-only buttons (a gear, a play triangle, a "+") that have no words. It returns boxes only;
naming them is uilabel's job.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Tuple

MODEL = Path(__file__).resolve().parent.parent / "models" / "omniparser-icon-detect-v2-1280.onnx"
SIZE = 1280
CONF = 0.05          # OmniParser's own defaults (UFO's omniparser_service: box_threshold 0.05, iou_threshold 0.1)
IOU = 0.1

_lock = threading.Lock()
_session = None

Box = Tuple[int, int, int, int, float]


def available() -> bool:
    return MODEL.is_file()


def _get():
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _session = ort.InferenceSession(str(MODEL), opts, providers=["CPUExecutionProvider"])
        return _session


def _letterbox(img):
    """RGB PIL image -> (1,3,SIZE,SIZE) float32, scale, (pad_x, pad_y): the same centred grey padding Ultralytics uses."""
    import numpy as np
    from PIL import Image
    w, h = img.size
    r = min(SIZE / w, SIZE / h)
    nw, nh = round(w * r), round(h * r)
    canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
    px, py = (SIZE - nw) // 2, (SIZE - nh) // 2
    canvas.paste(img.convert("RGB").resize((nw, nh), Image.BILINEAR), (px, py))
    arr = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return arr, r, (px, py)


def nms(boxes, scores, iou: float = IOU) -> List[int]:
    """Greedy non-maximum suppression; indices kept, best score first."""
    import numpy as np
    order = scores.argsort()[::-1]
    keep = []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    area = (x2 - x1) * (y2 - y1)
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        overlap = inter / (area[i] + area[order[1:]] - inter + 1e-9)
        order = order[1:][overlap <= iou]
    return keep


def detect(img, conf: float = CONF, iou: float = IOU, session=None) -> List[Box]:
    """Icon boxes [(x1, y1, x2, y2, score)] in the image's own pixel coordinates, best first."""
    import numpy as np
    arr, r, (px, py) = _letterbox(img)
    sess = session or _get()
    out = sess.run(None, {sess.get_inputs()[0].name: arr})[0][0]       # (5, N): cx, cy, w, h, score
    out = out.T
    out = out[out[:, 4] >= conf]
    if not len(out):
        return []
    cx, cy, w, h, s = out[:, 0], out[:, 1], out[:, 2], out[:, 3], out[:, 4]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    keep = nms(boxes, s, iou)
    W, H = img.size
    found = []
    for i in keep:
        x1, y1, x2, y2 = boxes[i]
        x1, x2 = [min(max((v - px) / r, 0), W) for v in (x1, x2)]
        y1, y2 = [min(max((v - py) / r, 0), H) for v in (y1, y2)]
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            found.append((round(x1), round(y1), round(x2), round(y2), round(float(s[i]), 3)))
    return found


# ---------------------------------------------------------------------------------------------------------------------------
# Naming: one local vision call names every new icon from a numbered contact sheet (Set-of-Mark, as UFO/OmniTool label
# screenshots). Measured 2026-11-09 with qwen3-vl:8b-instruct: 40 icons in 17 s cold, 10 icons in 1.2 s warm; function icons
# (back, close, more, volume, search, settings, bluetooth) named right, app logos often wrong (Brave -> "Adobe"), so an icon
# name is a hint for the loop, which still checks what happened after every click.
MAX_ICONS = 30
MAX_SIDE = 160                 # bigger boxes are cards/panels; their words already come from OCR
_names: dict = {}              # sha1 of the icon's pixels -> name ("" = not a button). Survives steps: only new icons are asked.

QUESTION = ("This sheet shows {n} small pieces cut from one application window, numbered 1 to {n}. For each number, name what "
            "clicking that piece would do or what it is, in 1 to 3 plain words (for example: settings, search, play, add, back, "
            "close, minimize, volume, more options). If a piece is not a button or icon, write \"none\". "
            "Reply as JSON only: {{\"1\": \"...\", ...}}.")


def enabled() -> bool:
    import os
    return available() and os.getenv("JARVIS_UI_ICONS", "on").lower() not in ("off", "0", "false", "no")


def wordless(boxes: List[Box], words: List[dict]) -> List[Box]:
    """Icon boxes with no OCR word inside (a word means OCR already named it) and button-sized, best first, at most MAX_ICONS."""
    out = []
    for b in boxes:
        w, h = b[2] - b[0], b[3] - b[1]
        if w > MAX_SIDE or h > MAX_SIDE:
            continue
        if any(b[0] <= x["center"][0] <= b[2] and b[1] <= x["center"][1] <= b[3] for x in words):
            continue
        out.append(b)
    return out[:MAX_ICONS]


def _key(crop) -> str:
    import hashlib
    return hashlib.sha1(crop.tobytes() + repr(crop.size).encode()).hexdigest()


def sheet(crops: list):
    """The numbered contact sheet (PIL image) for these icon crops."""
    from PIL import Image, ImageDraw, ImageFont
    cell, cols = 112, 8
    rows = (len(crops) + cols - 1) // cols
    out = Image.new("RGB", (cols * cell, rows * (cell + 24)), "white")
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("arialbd.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for i, crop in enumerate(crops):
        c = crop.copy()
        c.thumbnail((cell - 8, cell - 8))
        x, y = (i % cols) * cell, (i // cols) * (cell + 24)
        out.paste(c, (x + (cell - c.width) // 2, y + 4 + (cell - 8 - c.height) // 2))
        draw.rectangle([x + 1, y + 1, x + cell - 2, y + cell + 22], outline="black")
        draw.text((x + cell // 2 - 8, y + cell), str(i + 1), fill="red", font=font)
    return out


def _parse(reply: str, n: int) -> List[str]:
    import json
    import re
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    names = []
    for i in range(1, n + 1):
        v = re.sub(r"[^A-Za-z0-9 &+\-']", "", str(data.get(str(i), "")).strip())[:30].strip()
        names.append("" if not v or v.lower() in ("none", "nothing", "unknown", "n/a") else v)
    return names


def _ask_vision(png_b64: str, question: str) -> str:
    from core import vision
    return vision.describe([png_b64], question, timeout=120)


def name_icons(img, boxes: List[Box], ask=None) -> List[str]:
    """A name per box ('' = not a button), asking the vision model only about icons it has not named before."""
    import base64
    import io
    pad = 6
    crops = [img.crop((max(b[0] - pad, 0), max(b[1] - pad, 0), min(b[2] + pad, img.width), min(b[3] + pad, img.height))) for b in boxes]
    keys = [_key(c) for c in crops]
    new = [i for i, k in enumerate(keys) if k not in _names]
    if new:
        buf = io.BytesIO()
        sheet([crops[i] for i in new]).save(buf, "PNG")
        reply = (ask or _ask_vision)(base64.b64encode(buf.getvalue()).decode(), QUESTION.format(n=len(new)))
        for i, name in zip(new, _parse(reply, len(new))):
            _names[keys[i]] = name
    return [_names.get(k, "") for k in keys]


def labels(img, words: List[dict], origin: Tuple[int, int] = (0, 0), ask=None, detect_fn=None) -> List[dict]:
    """Named icon labels for a window image, shaped like OCR labels: [{text: 'Back icon', center: [x, y] (screen), confidence,
    icon: True}]. Same-named icons are numbered left to right, top to bottom ('More options icon 2'). [] on any failure."""
    try:
        words_local = [{**w, "center": [w["center"][0] - origin[0], w["center"][1] - origin[1]]} for w in words]
        boxes = wordless((detect_fn or detect)(img), words_local)
        if not boxes:
            return []
        names = name_icons(img, boxes, ask)
    except Exception:  # noqa: BLE001  (no model, no vision server: OCR words alone, as before)
        return []
    named = sorted([(b, n) for b, n in zip(boxes, names) if n], key=lambda p: (p[0][1] // 12, p[0][0]))
    seen: dict = {}
    out = []
    for b, n in named:
        base = n[:1].upper() + n[1:] + " icon"
        seen[base.lower()] = seen.get(base.lower(), 0) + 1
        text = base if seen[base.lower()] == 1 else f"{base} {seen[base.lower()]}"
        out.append({"text": text, "center": [(b[0] + b[2]) // 2 + origin[0], (b[1] + b[3]) // 2 + origin[1]],
                    "confidence": b[4], "icon": True})
    return out
