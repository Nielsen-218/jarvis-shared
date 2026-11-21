"""Tests for free local screen reading (core/pcvision.py).

One test exercises REAL RapidOCR on an in-memory image (no display needed); the rest use
fakes so they are deterministic and don't require a screen, pywinauto or a real desktop.
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core import pcvision


def _box(x=0, y=0):
    return [[x, y], [x + 10, y], [x + 10, y + 10], [x, y + 10]]


def test_ocr_reads_synthetic_image():
    img = Image.new("RGB", (460, 70), "white")
    ImageDraw.Draw(img).text((12, 25), "Open Settings and Save", fill="black")
    lines, note = pcvision.ocr_image(img)
    assert note is None
    joined = " ".join(l["text"] for l in lines).lower()
    assert "settings" in joined and "save" in joined
    assert all("center" in l and len(l["center"]) == 2 for l in lines)


def test_ocr_filters_secret_lines(monkeypatch):
    fake = lambda arr: ([(_box(), "password: hunter2", 0.99), (_box(0, 20), "Welcome", 0.98)], None)
    monkeypatch.setattr(pcvision, "_get_ocr", lambda: fake)
    lines, note = pcvision.ocr_image(Image.new("RGB", (10, 10)))
    texts = [l["text"] for l in lines]
    assert "Welcome" in texts
    assert not any("password" in t.lower() for t in texts)


def test_ocr_center_offset_by_region_origin(monkeypatch):
    fake = lambda arr: ([(_box(0, 0), "Hi", 0.9)], None)
    monkeypatch.setattr(pcvision, "_get_ocr", lambda: fake)
    lines, _ = pcvision.ocr_image(Image.new("RGB", (10, 10)), origin=(100, 200))
    assert lines[0]["center"] == [5 + 100, 5 + 200]


def test_summarize_lists_controls_then_text():
    data = {"uia": [{"name": "Save", "type": "Button"}],
            "ocr": [{"text": "File saved", "confidence": 0.9, "center": [1, 2]}]}
    text = pcvision.summarize(data)
    assert "Save [Button]" in text and "File saved" in text


def test_summarize_reports_when_empty():
    assert "No local screen text" in pcvision.summarize({"notes": ["uia_no_window"]})


def test_find_prefers_exact_uia_then_substring_then_ocr():
    data = {
        "uia": [{"name": "Save As", "type": "MenuItem", "center": [10, 10]},
                {"name": "Save", "type": "Button", "center": [20, 20]}],
        "ocr": [{"text": "unsaved changes", "confidence": 0.9, "center": [30, 30]}],
    }
    assert pcvision.find("Save", data)["center"] == [20, 20]          # exact UIA wins
    assert pcvision.find("Save A", data)["center"] == [10, 10]        # UIA substring
    assert pcvision.find("unsaved", data)["source"] == "ocr"          # falls to OCR
    assert pcvision.find("nonexistent", data) is None


def test_read_screen_degrades_without_crashing(monkeypatch):
    def boom(region):
        raise RuntimeError("no display")
    monkeypatch.setattr(pcvision, "_grab", boom)
    out = pcvision.read_screen(want_uia=False)   # no process -> skip UIA; capture fails
    assert out["sources"] == []
    assert any("capture_failed" in n for n in out["notes"])
    assert "No local screen text" in out["text"]


def test_ocr_warm_up_never_raises(monkeypatch):
    """The first screen read paid 14 s for model load on the owner's PC; warm_up pays it at startup, off the turn."""
    from core import pcvision
    monkeypatch.setattr(pcvision, "_get_ocr", lambda: (lambda img: ([], None)))
    assert isinstance(pcvision.warm_up(), float)
    monkeypatch.setattr(pcvision, "_get_ocr", lambda: (_ for _ in ()).throw(ImportError("no rapidocr")))
    assert pcvision.warm_up() is None
