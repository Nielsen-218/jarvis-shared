"""Mark VI part 3 (2026-11-10), KNOWN-ISSUES #11: a window is seen behind other windows (PrintWindow), never brought forward.

The win32 calls are faked: no window is captured and nothing opens. A minimized window, a failed PrintWindow or an all-black
picture is a structured BackgroundUnavailable("background_occluded"), and the judge then gets no picture (not judged, never
success). screenjudge.grab_window and uia.screen_labels use the capture in background mode and never call uia.focus.
"""
import pytest

from core import pcvision
from jarvis_pc.guardrails.stepfence import BackgroundUnavailable, background_only


class _Bitmap:
    def __init__(self, w, h, pixel):
        self.w, self.h, self.pixel = w, h, pixel

    def CreateCompatibleBitmap(self, dc, w, h):
        self.w, self.h = w, h

    def GetInfo(self):
        return {"bmWidth": self.w, "bmHeight": self.h}

    def GetBitmapBits(self, as_bytes):
        return bytes(self.pixel) * (self.w * self.h)        # BGRX per pixel

    def GetHandle(self):
        return 77


class _DC:
    def __init__(self, log):
        self.log = log

    def CreateCompatibleDC(self):
        return _DC(self.log)

    def SelectObject(self, obj):
        pass

    def GetSafeHdc(self):
        return 9

    def DeleteDC(self):
        self.log.append("DeleteDC")


@pytest.fixture
def fake_win32(monkeypatch):
    import win32gui
    import win32ui
    state = {"iconic": False, "printed": 1, "pixel": (40, 80, 120, 0), "rect": (100, 50, 140, 80), "log": []}
    monkeypatch.setattr(win32gui, "IsIconic", lambda h: state["iconic"])
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda h: state["rect"])
    monkeypatch.setattr(win32gui, "GetWindowDC", lambda h: 5)
    monkeypatch.setattr(win32gui, "ReleaseDC", lambda h, dc: state["log"].append("ReleaseDC"))
    monkeypatch.setattr(win32gui, "DeleteObject", lambda h: state["log"].append("DeleteObject"))
    monkeypatch.setattr(win32ui, "CreateDCFromHandle", lambda dc: _DC(state["log"]))
    monkeypatch.setattr(win32ui, "CreateBitmap", lambda: _Bitmap(0, 0, state["pixel"]))
    monkeypatch.setattr(pcvision, "_print_window", lambda hwnd, hdc: state["log"].append(("PrintWindow", hwnd)) or state["printed"])
    return state


def test_a_painted_window_comes_back_as_its_own_picture_and_everything_is_released(fake_win32):
    img = pcvision.grab_window_bg(1234)
    assert img.size == (40, 30)
    assert img.getpixel((0, 0)) == (120, 80, 40)              # BGRX -> RGB
    assert ("PrintWindow", 1234) in fake_win32["log"]
    assert fake_win32["log"].count("DeleteDC") == 2 and "ReleaseDC" in fake_win32["log"] and "DeleteObject" in fake_win32["log"]


def test_a_black_bitmap_is_occluded_not_a_picture(fake_win32):
    fake_win32["pixel"] = (0, 0, 0, 0)
    with pytest.raises(BackgroundUnavailable) as caught:
        pcvision.grab_window_bg(1234)
    assert caught.value.what == "background_occluded" and caught.value.kind == "background_unavailable"
    assert "ReleaseDC" in fake_win32["log"]


def test_a_minimized_window_raises_before_any_capture(fake_win32):
    fake_win32["iconic"] = True
    with pytest.raises(BackgroundUnavailable, match="minimized"):
        pcvision.grab_window_bg(1234)
    assert fake_win32["log"] == []


def test_a_failed_printwindow_raises_and_still_releases(fake_win32):
    fake_win32["printed"] = 0
    with pytest.raises(BackgroundUnavailable, match="background_occluded"):
        pcvision.grab_window_bg(1234)
    assert "ReleaseDC" in fake_win32["log"] and fake_win32["log"].count("DeleteDC") == 2


class _Win:
    handle = 1234

    def wrapper_object(self):
        return self


def _no_focus(monkeypatch):
    from jarvis_pc.uiact import uia
    focused = []
    monkeypatch.setattr(uia, "focus", lambda w: focused.append(w))
    monkeypatch.setattr(uia, "window", lambda title: _Win())
    monkeypatch.setattr(uia, "_in_front", lambda hwnd: False)
    monkeypatch.setattr(uia, "rect", lambda win: (100, 50, 140, 80))
    monkeypatch.setattr(pcvision, "_grab", lambda region: pytest.fail("a screen grab reads whatever is on top"))
    return focused


def test_screenjudge_looks_behind_other_windows_in_background_and_never_focuses(fake_win32, monkeypatch):
    from core import screenjudge
    focused = _no_focus(monkeypatch)
    with background_only():
        img = screenjudge.grab_window("Untitled - Notepad")
        assert img is not None and img.size == (40, 30)
        fake_win32["pixel"] = (0, 0, 0, 0)
        assert screenjudge.grab_window("Untitled - Notepad") is None     # black: not judged
        fake_win32["iconic"] = True
        assert screenjudge.grab_window("Untitled - Notepad") is None     # minimized: not judged
    assert focused == []
    assert screenjudge.judge("type hello", None, img) is None             # no picture is never a success


def test_screen_labels_ocr_the_capture_and_move_its_points_into_screen_coordinates(fake_win32, monkeypatch):
    from jarvis_pc.uiact import uia
    focused = _no_focus(monkeypatch)
    uia._last_read.update(key=None, labels=[])
    seen = {}

    def ocr(img, origin=(0, 0)):
        seen["size"], seen["origin"] = img.size, origin
        return [{"text": "Save", "confidence": 0.9, "center": [10 + origin[0], 5 + origin[1]]},
                {"text": "elsewhere", "confidence": 0.9, "center": [900, 900]}], None
    monkeypatch.setattr(pcvision, "ocr_image", ocr)
    from core import uiicons
    monkeypatch.setattr(uiicons, "enabled", lambda: False)          # no icon model in a unit test
    monkeypatch.setattr(pcvision, "read_screen", lambda **k: pytest.fail("the screen is not read in background"))
    with background_only():
        labels = uia.screen_labels(_Win())
    assert seen == {"size": (40, 30), "origin": (100, 50)}
    assert labels == [{"text": "Save", "confidence": 0.9, "center": [110, 55]}]
    assert focused == []
    fake_win32["iconic"] = True
    with background_only(), pytest.raises(BackgroundUnavailable):
        uia.screen_labels(_Win())
    uia._last_read.update(key=None, labels=[])
