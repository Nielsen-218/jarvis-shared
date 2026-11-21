"""Icon-only buttons for windows UI Automation cannot read (Mark III, 2026-11-09): OmniParser v2's detector (ONNX) finds the
boxes, one local vision call names the new ones from a numbered sheet, and they join the OCR words as '<name> icon' labels."""
import numpy as np
import pytest
from PIL import Image

from core import uiicons


@pytest.fixture(autouse=True)
def _fresh_names(monkeypatch):
    monkeypatch.setattr(uiicons, "_names", {})


class FakeSession:
    """Answers like the exported model: (1, 5, N) rows cx, cy, w, h, score in the 1280x1280 letterboxed frame."""
    def __init__(self, rows):
        self.rows = np.array(rows, dtype=np.float32).T[None]

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def run(self, outputs, feeds):
        assert feeds["images"].shape == (1, 3, 1280, 1280)
        return [self.rows]


def test_detect_undoes_the_letterbox_and_drops_overlaps_and_weak_boxes():
    img = Image.new("RGB", (1920, 1080), "white")
    # 1920x1080 -> scale 2/3, 1280x720, padded 280 px top and bottom. A 30x30 icon at (300, 300) is (200, 480)-(220, 500).
    rows = [[210, 490, 20, 20, 0.9], [211, 491, 20, 20, 0.5], [600, 600, 40, 40, 0.01]]
    got = uiicons.detect(img, session=FakeSession(rows))
    assert got == [(300, 300, 330, 330, 0.9)]


def test_nms_keeps_separate_boxes():
    boxes = np.array([[0, 0, 10, 10], [50, 50, 60, 60], [1, 1, 11, 11]], dtype=np.float32)
    assert uiicons.nms(boxes, np.array([0.5, 0.8, 0.4])) == [1, 0]


def test_only_wordless_button_sized_boxes_are_named():
    boxes = [(10, 10, 40, 40, 0.9), (100, 10, 300, 200, 0.9), (50, 50, 80, 80, 0.8)]
    words = [{"text": "Add", "center": [60, 60]}]
    assert uiicons.wordless(boxes, words) == [(10, 10, 40, 40, 0.9)]


def test_labels_are_named_numbered_and_in_screen_coordinates():
    img = Image.new("RGB", (400, 300), "white")
    for x in (10, 100, 200):
        img.paste((x % 255, 30, 90), (x, 10, x + 30, 40))           # three different-looking icons
    asked = []

    def ask(png, question):
        asked.append(question)
        return '{"1": "more options", "2": "none", "3": "More options"}'
    boxes = [(10, 10, 40, 40, 0.9), (100, 10, 130, 40, 0.8), (200, 10, 230, 40, 0.7)]
    out = uiicons.labels(img, [], origin=(1000, 500), ask=ask, detect_fn=lambda im: boxes)
    assert [x["text"] for x in out] == ["More options icon", "More options icon 2"]
    assert out[0]["center"] == [1025, 525] and out[0]["icon"] is True
    assert "3 small pieces" in asked[0]


def test_an_icon_is_named_once_then_remembered():
    img = Image.new("RGB", (200, 100), "white")
    img.paste((200, 0, 0), (10, 10, 40, 40))
    calls = []
    ask = lambda png, q: calls.append(q) or '{"1": "settings"}'   # noqa: E731
    box = [(10, 10, 40, 40, 0.9)]
    for _ in range(3):
        assert uiicons.labels(img, [], ask=ask, detect_fn=lambda im: box)[0]["text"] == "Settings icon"
    assert len(calls) == 1


def test_any_failure_leaves_just_the_words():
    def boom(png, q):
        raise OSError("vision server down")
    img = Image.new("RGB", (100, 100), "white")
    assert uiicons.labels(img, [], ask=boom, detect_fn=lambda im: [(1, 1, 20, 20, 0.9)]) == []


def test_a_garbled_reply_names_nothing():
    assert uiicons._parse("sorry, I cannot", 2) == ["", ""]
    assert uiicons._parse('{"1": "Back <script>", "2": "unknown"}', 2) == ["Back script", ""]


def test_switched_off_by_setting(monkeypatch):
    monkeypatch.setenv("JARVIS_UI_ICONS", "off")
    assert uiicons.enabled() is False


@pytest.mark.skipif(not uiicons.available(), reason="icon model not downloaded on this machine")
def test_the_real_model_finds_a_drawn_button():
    img = Image.new("RGB", (800, 600), (240, 240, 240))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((380, 280, 420, 320), radius=6, fill=(30, 110, 220))
    d.polygon([(392, 290), (392, 310), (410, 300)], fill="white")      # a play button
    got = uiicons.detect(img)
    assert any(b[0] <= 400 <= b[2] and b[1] <= 300 <= b[3] for b in got)


def test_screen_reading_adds_named_icons_after_the_words(monkeypatch):
    from core import pcvision
    from jarvis_pc.uiact import uia
    monkeypatch.setenv("JARVIS_UI_ICONS", "on")
    monkeypatch.setattr(uiicons, "available", lambda: True)
    monkeypatch.setattr(uia, "rect", lambda w: (100, 100, 500, 400))
    monkeypatch.setattr(uia, "_hwnd", lambda w: 1)
    monkeypatch.setattr(uia, "_in_front", lambda h: True)
    monkeypatch.setattr(pcvision, "read_screen", lambda **kw: {"ocr": [{"text": "File", "center": [120, 110], "confidence": 0.9}]})
    img = Image.new("RGB", (400, 300), "white")
    img.paste((0, 90, 200), (300, 10, 330, 40))
    monkeypatch.setattr(pcvision, "_grab", lambda region: (img, (100, 100)))
    monkeypatch.setattr(uiicons, "detect", lambda im: [(300, 10, 330, 40, 0.9)])
    monkeypatch.setattr(uiicons, "_ask_vision", lambda png, q: '{"1": "settings"}')
    out = uia.screen_labels(object())
    assert [x["text"] for x in out] == ["File", "Settings icon"] and out[1]["center"] == [415, 125]


def test_screen_reading_without_the_icon_model_is_unchanged(monkeypatch):
    from core import pcvision
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(uiicons, "available", lambda: False)
    monkeypatch.setattr(uia, "rect", lambda w: (100, 100, 500, 400))
    monkeypatch.setattr(uia, "_hwnd", lambda w: 1)
    monkeypatch.setattr(uia, "_in_front", lambda h: True)
    monkeypatch.setattr(pcvision, "read_screen", lambda **kw: {"ocr": [{"text": "File", "center": [120, 110], "confidence": 0.9}]})
    assert [x["text"] for x in uia.screen_labels(object())] == ["File"]


def test_icons_keep_their_room_when_a_window_is_full_of_words(monkeypatch):
    from jarvis_pc.uiact import agent, uia
    labels = [{"text": f"word{i}", "center": [i, 1]} for i in range(110)] + \
             [{"text": f"Tool{i} icon", "center": [i, 2], "icon": True} for i in range(30)]
    monkeypatch.setattr(uia, "window", lambda t: (_ for _ in ()).throw(uia.UIError("no tree")))
    monkeypatch.setattr(agent, "time", __import__("types").SimpleNamespace(sleep=lambda s: None))
    calls = {"n": 0}

    def window(title):
        calls["n"] += 1
        if calls["n"] == 1:
            raise uia.UIError("no tree")             # UI Automation sees nothing
        return object()
    monkeypatch.setattr(uia, "window", window)
    monkeypatch.setattr(uia, "screen_labels", lambda w: labels)
    view = agent.observe("Blender", limit=90)
    assert view["_vision"] and len(view["click"]) == 90
    assert sum(n.endswith("icon") for n in view["click"]) == 30


def test_the_same_pixels_are_read_once_and_any_change_reads_again(monkeypatch):
    from core import pcvision
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(uia, "_last_read", {"key": None, "labels": []})
    monkeypatch.setattr(uiicons, "available", lambda: False)
    monkeypatch.setattr(uia, "rect", lambda w: (100, 100, 500, 400))
    monkeypatch.setattr(uia, "_hwnd", lambda w: 1)
    monkeypatch.setattr(uia, "_in_front", lambda h: True)
    screen = {"img": Image.new("RGB", (400, 300), "white")}
    monkeypatch.setattr(pcvision, "_grab", lambda region: (screen["img"], (100, 100)))
    reads = []
    monkeypatch.setattr(pcvision, "read_screen",
                        lambda **kw: reads.append(1) or {"ocr": [{"text": f"File{len(reads)}", "center": [120, 110]}]})
    assert uia.screen_labels(object())[0]["text"] == "File1"
    assert uia.screen_labels(object())[0]["text"] == "File1" and len(reads) == 1      # observe then tap: one reading
    screen["img"] = Image.new("RGB", (400, 300), "black")                               # the window changed
    assert uia.screen_labels(object())[0]["text"] == "File2" and len(reads) == 2
