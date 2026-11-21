"""Free, local screen understanding: read what's on screen without paid AI.

Two complementary local sources, both offline:
  * UIA (pywinauto)  -> real UI element names/types/positions for native Windows apps.
  * OCR  (RapidOCR)  -> text + boxes for anything UIA can't expose (games, canvases, web).

Everything runs on the loopback machine; nothing is sent to a cloud model and nothing is
written to disk. Password / financial-credential fields are never reported. If a backend
is missing it degrades gracefully (returns what it can plus a note) instead of raising, so
callers can always fall back to paid vision only as a last resort.
"""
import copy
import re
import time

_SECRET = re.compile(
    r"password|credit.?card|card.?number|security.?code|cvv|secret|api.?key|bank.?account|passcode|pin\b",
    re.I)

_ocr = None  # lazy RapidOCR singleton (model load is ~1s; reuse it)
_observations = {}
_CACHE_SECONDS = 1.5


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def warm_up() -> float | None:
    """Load the OCR models and run them once, so the first real read doesn't pay for it. Measured on the owner's PC
    (1920x1080, a text-heavy screen): the first read_screen took 14.1 s, the next full pass 3.5 s; detection alone
    is 1.9 s, so the difference is one-time model load and first-run graph setup. Returns the seconds it took, or
    None when OCR isn't installed. Safe to call from a background thread at startup."""
    import time
    try:
        import numpy as np
        started = time.monotonic()
        ocr = _get_ocr()
        canvas = np.full((160, 480, 3), 255, dtype=np.uint8)
        canvas[60:100, 40:440] = 0                                  # a dark bar: something for detection to find
        ocr(canvas)
        return time.monotonic() - started
    except Exception:
        return None


def _center(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return [round(sum(xs) / len(xs)), round(sum(ys) / len(ys))]


def ocr_image(img, origin=(0, 0)):
    """Run local OCR on a PIL image. Returns [{text, confidence, center:[x,y]}] in screen
    coordinates (origin is the capture's top-left on screen). Never raises for OCR issues."""
    try:
        import numpy as np
        result, _ = _get_ocr()(np.array(img.convert("RGB")))
    except Exception as error:  # missing dep, model load, decode -> report, don't crash
        return [], f"ocr_unavailable: {type(error).__name__}"
    lines = []
    for box, text, conf in (result or []):
        text = (text or "").strip()
        if not text or _SECRET.search(text):
            continue
        cx, cy = _center(box)
        lines.append({"text": text, "confidence": round(float(conf), 3),
                      "center": [cx + origin[0], cy + origin[1]]})
    return lines, None


def _grab(region):
    from PIL import ImageGrab
    if region:
        x, y, w, h = region
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        return img, (x, y)
    return ImageGrab.grab(all_screens=False), (0, 0)


PW_RENDERFULLCONTENT = 2       # PrintWindow flag: DirectComposition / WinUI / Chromium windows render black without it


def _print_window(hwnd, hdc) -> int:
    import ctypes
    return int(ctypes.windll.user32.PrintWindow(int(hwnd), int(hdc), PW_RENDERFULLCONTENT))


def grab_window_bg(hwnd):
    """The window's OWN pixels, even when other windows cover it, without bringing it to the front (KNOWN-ISSUES #11,
    2026-11-10, Mark VI: a mission never takes the foreground). PrintWindow asks the window to paint itself into our bitmap.
    Raises jarvis_pc.guardrails.stepfence.BackgroundUnavailable("background_occluded") when that gives nothing usable: a
    minimized window (it renders black), a failed PrintWindow, or an all-black picture. Never falls back to a screen grab,
    which would read whatever is on top."""
    import win32gui
    import win32ui
    from PIL import Image
    from jarvis_pc.guardrails.stepfence import BackgroundUnavailable
    if win32gui.IsIconic(hwnd):
        raise BackgroundUnavailable("background_occluded", "the window is minimized, so I can't see it without you")
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise BackgroundUnavailable("background_occluded", "the window has no size to look at")
    hwdc = win32gui.GetWindowDC(hwnd)
    src = mem = bmp = None
    try:
        src = win32ui.CreateDCFromHandle(hwdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        ok = _print_window(hwnd, mem.GetSafeHdc())
        if not ok:
            raise BackgroundUnavailable("background_occluded", "the window would not paint itself for me")
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    finally:
        for done in (lambda: win32gui.DeleteObject(bmp.GetHandle()), lambda: mem.DeleteDC(), lambda: src.DeleteDC(),
                     lambda: win32gui.ReleaseDC(hwnd, hwdc)):
            try:
                done()
            except Exception:  # noqa: BLE001  (None parts of a half-built capture)
                pass
    if img.convert("L").getextrema()[1] == 0:
        raise BackgroundUnavailable("background_occluded", "the window came back black")
    return img


def uia_elements(process=None, window=None, limit=200):
    """Enumerate visible UIA elements (name/type/center/enabled) for a named window.
    Returns ([], note) when pywinauto is missing or no single window matches."""
    try:
        from pywinauto import Desktop
    except ImportError:
        return [], "uia_unavailable: pywinauto not installed"
    try:
        import psutil
        desks = []
        if process:
            for proc in psutil.process_iter(["name"]):
                if (proc.info["name"] or "").casefold() == process.casefold():
                    desks.extend(Desktop(backend="uia").windows(process=proc.pid, visible_only=True,
                                                                **({"title": window} if window else {})))
        else:
            desks = Desktop(backend="uia").windows(visible_only=True, active_only=True)
        if not desks:
            return [], "uia_no_window"
        elements = []
        for win in desks[:3]:
            for el in win.descendants():
                try:
                    info = el.element_info
                    name = (info.name or "").strip()
                    if not name or _SECRET.search(name) or getattr(info.element, "CurrentIsPassword", False):
                        continue
                    if not (el.is_visible() and el.is_enabled()):
                        continue
                    r = info.rectangle
                    elements.append({"name": name[:120], "type": info.control_type,
                                     "center": [(r.left + r.right) // 2, (r.top + r.bottom) // 2],
                                     "enabled": True})
                    if len(elements) >= limit:
                        return elements, None
                except Exception:
                    continue
        return elements, None
    except Exception as error:
        return [], f"uia_error: {type(error).__name__}"


def _cached(key):
    hit = _observations.get(key)
    if hit and time.monotonic() - hit[0] <= _CACHE_SECONDS:
        data = copy.deepcopy(hit[1])
        data["cached"] = True
        data["age_ms"] = round((time.monotonic() - hit[0]) * 1000)
        return data
    _observations.pop(key, None)
    return None


def clear_cache():
    """Forget short-lived UI observations after a known UI-changing action."""
    _observations.clear()


def read_screen(process=None, window=None, region=None, want_ocr=True, want_uia=True):
    """Read the screen locally. Returns a dict with a human-readable `text` summary plus
    structured `ocr` lines and `uia` elements (both with screen-coordinate centers), the
    `sources` that contributed and any `notes`. Nothing is sent to a cloud model."""
    key = (process or "", window or "", tuple(region) if region else None, bool(want_ocr), bool(want_uia))
    if cached := _cached(key):
        return cached
    out = {"ocr": [], "uia": [], "sources": [], "notes": [], "cached": False}
    if want_uia and (process or window):
        els, note = uia_elements(process, window)
        if els:
            out["uia"] = els
            out["sources"].append("uia")
        if note:
            out["notes"].append(note)
    # UIA is both faster and more exact for a native target.  Do not then spend
    # seconds OCRing the whole desktop unless the caller narrowed a region or
    # UIA yielded nothing. This avoids repeatedly reading unrelated windows.
    use_ocr = want_ocr and (region is not None or not out["uia"])
    if use_ocr:
        try:
            img, origin = _grab(region)
            lines, note = ocr_image(img, origin)
            if lines:
                out["ocr"] = lines
                out["sources"].append("ocr")
            if note:
                out["notes"].append(note)
        except Exception as error:
            out["notes"].append(f"capture_failed: {type(error).__name__}")
    out["text"] = summarize(out)
    _observations[key] = (time.monotonic(), copy.deepcopy(out))
    return out


def summarize(data, max_items=60):
    """Compact readable text for the brain/local planner: UIA controls first, then OCR text."""
    parts = []
    if data.get("uia"):
        controls = ", ".join(f"{e['name']} [{e['type']}]" for e in data["uia"][:max_items])
        parts.append("On-screen controls: " + controls)
    if data.get("ocr"):
        text = " | ".join(line["text"] for line in data["ocr"][:max_items])
        parts.append("Visible text: " + text)
    if not parts:
        note = "; ".join(data.get("notes", [])) or "nothing readable"
        return f"No local screen text found ({note})."
    return "\n".join(parts)


def find(query, data=None, **read_kw):
    """Best on-screen match for `query` (for clicking by label). Prefers an exact UIA name,
    then OCR text containing the query. Returns {source, text, center} or None."""
    data = data or read_screen(**read_kw)
    q = query.strip().casefold()
    for el in data.get("uia", []):
        if el["name"].casefold() == q:
            return {"source": "uia", "text": el["name"], "center": el["center"]}
    for el in data.get("uia", []):
        if q in el["name"].casefold():
            return {"source": "uia", "text": el["name"], "center": el["center"]}
    for line in sorted(data.get("ocr", []), key=lambda line_item: -line_item["confidence"]):
        if q in line["text"].casefold():
            return {"source": "ocr", "text": line["text"], "center": line["center"]}
    return None
